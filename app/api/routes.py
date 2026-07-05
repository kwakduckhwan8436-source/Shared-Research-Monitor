"""REST 라우트.

엔드포인트:
  GET  /api/health
  GET  /api/horizons
  GET  /api/universe?limit=&q=                       # 전종목 목록(검색/상한)
  GET  /api/recommendations/{horizon}?top_n=&refresh=&q=
  GET  /api/recommendation/{symbol}/{horizon}        # 단건 + LLM 근거 설명
  GET  /api/diagnostics/{symbol}/{horizon}           # 점수 계산 과정(검증)
  GET  /api/verdict/calibration
  POST /api/verdict/evaluate
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from app.llm import explain as explain_mod

BUILD_VERSION = "2026.06.30-time2"   # 서버가 새 코드로 떴는지 확인용(health.v / presence.v)


def _rsi_series(values: list, period: int = 14) -> list:
    """누적 수급 등 시계열의 RSI(0~100) 오실레이터. 입력 길이만큼 반환(앞부분 None)."""
    n = len(values)
    if n < 2:
        return [None] * n
    period = max(2, min(period, n - 1))
    out: list = [None] * n
    gains, losses = 0.0, 0.0
    # 초기 평균
    for i in range(1, period + 1):
        ch = values[i] - values[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    avg_g, avg_l = gains / period, losses / period
    def _rsi(g, l):
        if l == 0:
            return 100.0 if g > 0 else 50.0
        rs = g / l
        return 100.0 - 100.0 / (1.0 + rs)
    out[period] = round(_rsi(avg_g, avg_l), 1)
    for i in range(period + 1, n):
        ch = values[i] - values[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
        out[i] = round(_rsi(avg_g, avg_l), 1)
    return out


def register_routes(app: Any, ctx: Any) -> None:
    from fastapi import HTTPException, Query, Request, Body
    from app.data.schema import Kind
    from app.signals.registry import HORIZONS

    SCAN = ctx.config.scan_limit

    # 공개 호스팅 모드: 시세 재배포 위험 엔드포인트는 비활성(법적 사유)
    _MARKETDATA_DISABLED = {
        "disabled": True,
        "reason": "공개 호스팅 모드에서는 시세·수급 데이터를 제공하지 않습니다(시세 재배포 제한). "
                  "공시·뉴스·커뮤니티·종목 검색을 이용하세요.",
        "public_mode": True,
    }

    def _md_blocked() -> bool:
        return bool(getattr(ctx.config, "public_mode", False))

    import datetime as _dt_mod
    def _t_rec_dt(s):
        try:
            return _dt_mod.datetime.fromisoformat(s)
        except Exception:
            return ctx.clock.now()

    # ===== 속도 제한(rate limit) + 도배 방지 — 메모리 기반(프로세스 내) =====
    import time as _rl_time
    import threading as _rl_th
    _rl_lock = _rl_th.Lock()
    _rl_hits: dict = {}        # key -> [timestamps]
    _rl_last_text: dict = {}   # key -> (text, ts)  동일 메시지 도배 방지

    def _client_key(request) -> str:
        """cid 우선, 없으면 IP. 둘 다 없으면 'anon'."""
        cid = ""
        ip = ""
        try:
            cid = (request.headers.get("x-client-id") or "") if request else ""
        except Exception:
            cid = ""
        try:
            if request and getattr(request, "client", None):
                ip = request.client.host or ""
            if request:
                xff = request.headers.get("x-forwarded-for") or ""
                if xff:
                    ip = xff.split(",")[0].strip()
        except Exception:
            pass
        return ("cid:" + cid) if cid else (("ip:" + ip) if ip else "anon")

    def _rate_check(key: str, action: str, limit: int, window: float) -> bool:
        """window(초) 안에 limit 회 초과면 False(차단)."""
        now = _rl_time.time()
        k = f"{action}:{key}"
        with _rl_lock:
            arr = _rl_hits.get(k, [])
            arr = [t for t in arr if now - t < window]
            if len(arr) >= limit:
                _rl_hits[k] = arr
                return False
            arr.append(now)
            _rl_hits[k] = arr
            # 메모리 누수 방지: 가끔 정리
            if len(_rl_hits) > 5000:
                for kk in list(_rl_hits.keys()):
                    _rl_hits[kk] = [t for t in _rl_hits[kk] if now - t < 3600]
                    if not _rl_hits[kk]:
                        _rl_hits.pop(kk, None)
            return True

    def _dup_check(key: str, text: str) -> bool:
        """같은 사용자가 동일 텍스트를 30초 내 반복하면 False(도배 차단)."""
        now = _rl_time.time()
        with _rl_lock:
            prev = _rl_last_text.get(key)
            _rl_last_text[key] = (text, now)
            if prev and prev[0] == text and (now - prev[1]) < 30:
                return False
            return True

    import json as _json_mod

    # 기본 금지어(욕설·시세조종 유도·불법 유도 일부). 운영자가 추가/수정 가능(settings).
    _DEFAULT_BANNED = ["씨발", "시발", "개새끼", "병신", "지랄", "좆", "닥쳐",
                       "작전주", "선취매 후 추천", "리딩방 가입", "단톡방 입장",
                       "비트코인 보내", "원금보장", "확정수익"]

    def _banned_words() -> list:
        try:
            row = ctx.store.get_setting("banned_words")
            if row and row.get("value"):
                return _json_mod.loads(row["value"])
        except Exception:
            pass
        return _DEFAULT_BANNED

    def _contains_banned(text: str):
        t = (text or "").replace(" ", "")
        for w in _banned_words():
            if w.replace(" ", "") and w.replace(" ", "") in t:
                return w
        return None

    def _is_admin(request, explicit_token: str = "") -> bool:
        tok = (getattr(ctx.config, "admin_token", "") or "").strip()
        if not tok:
            return False
        hdr = ""
        try:
            hdr = (request.headers.get("x-admin-token") or "").strip()
        except Exception:
            hdr = ""
        if not hdr and explicit_token:
            hdr = explicit_token.strip()
        return hdr == tok

    def _is_admin_token(token: str) -> bool:
        """request 객체 없이 토큰 문자열만으로 검증(환경 호환)."""
        tok = (getattr(ctx.config, "admin_token", "") or "").strip()
        if not tok:
            return False
        return (token or "").strip() == tok

    @app.get("/api/feed/status")
    def feed_status() -> dict:
        """피드 진단 — 각 피드에 데이터가 있는지/마지막 오류가 뭔지 노출.
        '전반적으로 안 살아난다' 할 때 원인(잠자기/키/RSS)을 파악하는 용도."""
        import time as _t
        now_t = _t.time()
        disc_n = len(_feed.get("mkt_disc") or [])
        disc_err = _feed.get("mkt_disc_err")
        disc_ok = _feed.get("mkt_disc_ok", 0.0)
        pol_n = len(_policy_cache.get("items") or [])
        pol_ok = _policy_cache.get("ok", 0.0)
        return {
            "disclosures": {"count": disc_n, "error": disc_err,
                            "age_sec": int(now_t - disc_ok) if disc_ok else None},
            "policy": {"count": pol_n,
                       "age_sec": int(now_t - pol_ok) if pol_ok else None},
            "dart_key_set": bool(getattr(ctx.config, "dart_api_key", "")),
            "naver_key_set": bool(getattr(ctx.config, "naver_client_id", "")),
            "note": ("데이터가 0이고 방금 접속했다면 무료플랜 잠자기에서 깨어나는 중입니다"
                     "(30초~1분 후 새로고침). 계속 0이면 키/RSS 점검이 필요합니다."),
        }

    @app.get("/api/health")
    def health() -> dict:
        from app.core.holidays import all_holiday_dates
        return {"status": "ok", "data_source": ctx.config.data_source,
                "version": BUILD_VERSION,
                "public_mode": ctx.config.public_mode,
                "universe_mode": ctx.config.universe_mode,
                "universe_size": len(ctx.search_universe or ctx.universe), "scan_limit": SCAN,
                "ssot_size": ctx.ssot.size(), "snapshot_id": ctx.ssot.snapshot_id(),
                "cafe_url": ctx.config.cafe_url, "cafe_name": ctx.config.cafe_name,
                # 키 인식 여부만 노출(값은 절대 안 나감 — 보안)
                "dart_key_set": bool(getattr(ctx.config, "dart_api_key", "")),
                "naver_key_set": bool(getattr(ctx.config, "naver_client_id", "")
                                      and getattr(ctx.config, "naver_client_secret", "")),
                "policy_news": bool(getattr(ctx, "policy_news", None)),
                "public_data_set": bool(getattr(ctx.config, "data_go_key", "")),
                "fx_set": bool(getattr(ctx.config, "exim_key", "")),
                "admin_set": bool(getattr(ctx.config, "admin_token", "")),
                "market_holidays": all_holiday_dates(),
                "llm": "anthropic" if ctx.llm_client else "offline"}

    @app.get("/api/horizons")
    def horizons() -> dict:
        return {"horizons": list(HORIZONS)}

    @app.get("/api/universe")
    def universe(limit: int = Query(100, ge=1, le=1000), q: str = "") -> dict:
        out = []
        for s in ctx.ssot.symbols():
            name = ctx.name_of(s)
            if q and q not in s and q not in name:
                continue
            dp = ctx.ssot.get(s, Kind.OHLCV.value)
            status = dp.payload.get("status") if dp else None
            price = dp.payload["bars"][-1]["c"] if dp and dp.payload.get("bars") else None
            out.append({"symbol": s, "name": name, "status": status, "price": price})
        total = len(out)
        return {"universe": out[:limit], "shown": min(limit, total), "count": total}

    def _refresh_if_needed(refresh: bool) -> None:
        if refresh:
            ctx.service.refresh_data(ctx.universe, ctx.all_kinds())

    import time as _t_rec
    _rec_cache: dict = {}   # horizon -> (ts, full_recs_d)

    @app.get("/api/recommendations/{horizon}")
    def recommendations(horizon: str,
                        top_n: int = Query(30, ge=1, le=100),
                        refresh: bool = False, q: str = "") -> dict:
        if _md_blocked():
            return {**_MARKETDATA_DISABLED, "items": []}
        if horizon not in HORIZONS:
            raise HTTPException(404, f"unknown horizon: {horizon}")
        _refresh_if_needed(refresh)
        # 호라이즌 전환 속도: 15초 캐시(전체 목록을 캐시하고, 검색은 캐시 위에서 필터)
        now_t = _t_rec.time()
        cached = _rec_cache.get(horizon)
        if cached and not refresh and now_t - cached[0] < 15:
            full = cached[1]
        else:
            recs = ctx.service.recommend(horizon, top_n=100, persist=True, scan_limit=SCAN)
            full = [asdict(r) for r in recs]
            _rec_cache[horizon] = (now_t, full)
        if q:
            recs_d = [r for r in full if q in r["symbol"] or q in (r["name"] or "")][:top_n]
        else:
            recs_d = full[:top_n]
        return {"horizon": horizon, "count": len(recs_d),
                "universe_size": len(ctx.universe), "scanned": SCAN,
                "snapshot_id": ctx.ssot.snapshot_id(),
                "weights_calibrated": full[0]["weights_calibrated"] if full else None,
                "recommendations": recs_d}

    _scr_cache: dict = {}

    def _refine_hot_live(rows: list, limit: int = 40) -> None:
        """핫 모드: 상위 종목의 일봉 등락률을 KIS 실시간 현재가로 덮어써 '지금 급등'을 잡는다.
        호출량 제어 위해 상위 limit 종목만. 실패는 무시(일봉값 유지)."""
        if ctx.quote is None or not hasattr(ctx.quote, "current_price"):
            return
        for r in rows[:limit]:
            try:
                qd = ctx.quote.current_price(r["symbol"])
                if qd.get("change_pct") is not None:
                    r["change_pct"] = qd["change_pct"]
                    r["live"] = True
                    if qd.get("price"):
                        r["ref_price"] = qd["price"]
            except Exception:
                pass

    @app.get("/api/screener/{mode}")
    def screener(mode: str, top_n: int = Query(100, ge=1, le=300),
                 refresh: bool = False, q: str = "", market: str = "",
                 live: bool = False,
                 cond_streak: int = Query(0, ge=0, le=20),
                 cond_high: bool = False, cond_align: bool = False) -> dict:
        """수급 스크리너 — foreign(외국인 순매수)/inst(기관 순매수). market=KOSPI|KOSDAQ 필터.
        복합 조건: cond_streak(N일 연속 순매수)·cond_high(신고가)·cond_align(정배열)."""
        if _md_blocked():
            return {**_MARKETDATA_DISABLED, "items": [], "mode": mode}
        if mode not in ("foreign", "inst"):
            raise HTTPException(404, f"unknown mode: {mode}")
        _refresh_if_needed(refresh)
        now_t = _t_rec.time()
        has_cond = bool(cond_streak or cond_high or cond_align)
        ck = (mode, market, cond_streak, cond_high, cond_align)
        if not q:
            cached = _scr_cache.get(ck)
            if cached and not refresh and now_t - cached[0] < 15:
                rows = cached[1]
            else:
                rows = ctx.service.screen(mode, top_n=top_n, market=market, scan_limit=SCAN,
                                          cond_streak=cond_streak, cond_high=cond_high,
                                          cond_align=cond_align)
                _scr_cache[ck] = (now_t, rows)
        else:
            pool = ctx.search_universe or list(ctx.universe)
            rows = ctx.service.screen(mode, top_n=top_n, q=q, market=market,
                                      scan_limit=SCAN, search_pool=pool)
        out = {"mode": mode, "market": market, "count": len(rows),
               "universe_size": len(ctx.search_universe or ctx.universe), "scanned": SCAN,
               "rank_universe_size": len(ctx.universe),
               "loaded": len(ctx.ssot.symbols()),
               "supply_loaded": sum(1 for s in ctx.ssot.symbols()
                                    if ctx.ssot.get(s, "supply") is not None),
               "items": rows}
        if not q:
            try:
                out["theme_flow"] = ctx.service.theme_money_flow(rows, top_n=5, by="net_buy")
            except Exception:
                out["theme_flow"] = []
        # 데이터 신선도 — 표시된 종목 중 가장 최근 OHLCV 적재 시각 + 장중 여부
        try:
            from app.core.clock import is_market_hours as _imh, to_kst as _tk
            now = ctx.clock.now()
            fetched = []
            for r in rows[:20]:
                dp = ctx.ssot.get(r["symbol"], "ohlcv")
                if dp and getattr(dp, "fetched_at", None):
                    fetched.append(dp.fetched_at)
            if fetched:
                latest = max(fetched)
                out["data_as_of"] = _tk(latest).strftime("%H:%M:%S")
                age = (now - latest).total_seconds()
                out["data_age_sec"] = int(age)
            out["market_hours"] = _imh(now)
        except Exception:
            pass
        return out

    @app.get("/api/briefing")
    def briefing() -> dict:
        """아침 시황 — 간밤 해외 + 국내 지수 + 국면 + 주도주/핫 + 주요 공시를 한 번에."""
        now = ctx.clock.now()
        out: dict = {"ts": now.isoformat(), "data_source": ctx.config.data_source}
        # 지수(국내+해외 간밤)
        try:
            out["indices"] = indices()
        except Exception:
            out["indices"] = {"domestic": [], "global": []}
        # 국면
        try:
            out["regime"] = regime()
        except Exception:
            out["regime"] = None
        # 수급 — 외국인 / 기관 순매수 상위 5
        try:
            out["foreign"] = ctx.service.screen("foreign", top_n=5, scan_limit=SCAN)
        except Exception:
            out["foreign"] = []
        try:
            out["inst"] = ctx.service.screen("inst", top_n=5, scan_limit=SCAN)
        except Exception:
            out["inst"] = []
        # 주요 공시(시장 전체 상위 8)
        try:
            if ctx.config.data_source != "mock":
                _market_disc_refresh(30)
                out["disclosures"] = _dedupe(_feed["mkt_disc"])[:8]
            else:
                out["disclosures"] = []
        except Exception:
            out["disclosures"] = []
        # 수급 집중 테마(외국인 순매수 기준)
        try:
            out["theme_flow"] = ctx.service.theme_money_flow(
                ctx.service.screen("foreign", top_n=40, scan_limit=SCAN),
                top_n=4, by="net_buy")
        except Exception:
            out["theme_flow"] = []
        # 자동 시황 문장(오프라인 규칙 기반)
        out["summary"] = _briefing_summary(out)
        # 밤새 해외/매크로 이슈 — CPI·PPI·금리·연준·해외 증시 뉴스
        try:
            out["macro_news"] = _macro_overnight_news()
        except Exception:
            out["macro_news"] = []
        return out

    _MACRO_Q = ["미국 CPI 물가", "미국 PPI 생산자물가", "연준 금리 FOMC",
                "미국 국채 금리", "나스닥 마감", "환율 원달러", "국제유가",
                "엔비디아 실적", "중국 경제", "비트코인"]
    _macro_cache = {"ts": 0.0, "data": []}

    def _macro_overnight_news(max_items: int = 12) -> list:
        """밤새 매크로/해외 이슈를 뉴스에서 모은다(CPI·PPI·금리·연준·해외증시).
        5분 캐시. 네이버+구글 병합, 최신순."""
        now_t = _time.time()
        if _macro_cache["data"] and now_t - _macro_cache["ts"] < 300:
            return _macro_cache["data"]
        if not _press_on() or ctx.config.data_source == "mock":
            return []
        now = ctx.clock.now()
        items: list = []
        seen = set()
        for q in _MACRO_Q:
            try:
                for a in _fetch_press(q, now)[:3]:
                    key = "".join((a.get("title") or "").split())[:50]
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    items.append({**a, "topic": q})
            except Exception:
                pass
        items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
        _macro_cache["ts"] = now_t
        _macro_cache["data"] = items[:max_items]
        return _macro_cache["data"]

    def _briefing_summary(b: dict) -> list:
        """지수·국면·테마를 묶어 한국어 시황 문장 생성(LLM 없이 규칙 기반)."""
        lines: list = []
        g = (b.get("indices") or {}).get("global") or []
        gmap = {}
        for x in g:
            nm = x.get("name", "")
            gmap[nm] = x.get("change_pct")
        # 간밤 미국
        us = [gmap.get(k) for k in ("나스닥", "S&P500", "다우") if gmap.get(k) is not None]
        if us:
            avg = sum(us) / len(us)
            tone = "강세" if avg > 0.4 else ("약세" if avg < -0.4 else "혼조")
            nq = gmap.get("나스닥")
            detail = f"(나스닥 {nq:+.1f}%)" if nq is not None else ""
            lines.append(f"간밤 미국 증시 {tone} {detail}".strip() + ".")
            dom_dir = "상승 출발 우호적" if avg > 0.4 else ("하락 압력" if avg < -0.4 else "방향성 제한적")
            lines.append(f"국내 증시는 {dom_dir}일 가능성.")
        # VIX / 환율
        vix = gmap.get("VIX")
        if vix is not None:
            risk = "위험선호" if vix < 0 else "위험회피"
            lines.append(f"변동성 지수(VIX) {vix:+.1f}% → {risk} 분위.")
        fx = gmap.get("원/달러")
        if fx is not None:
            won = "원화 약세(수출주 우호)" if fx > 0 else "원화 강세"
            lines.append(f"원/달러 {fx:+.1f}% → {won}.")
        # 국면
        rg = b.get("regime") or {}
        if rg.get("label"):
            lines.append(f"현재 시장 국면: {rg['label']}.")
        # 테마 자금
        tf = b.get("theme_flow") or []
        if tf:
            names = "·".join(t["theme"] for t in tf[:3])
            lines.append(f"외국인 수급은 {names} 테마에 집중되는 흐름.")
        if not lines:
            lines.append("데이터를 모으는 중입니다. 라이브 연결 후 시황이 채워집니다.")
        return lines

    @app.get("/api/recommendation/{symbol}/{horizon}")
    def recommendation_detail(symbol: str, horizon: str) -> dict:
        if horizon not in HORIZONS:
            raise HTTPException(404, f"unknown horizon: {horizon}")
        recs = ctx.service.recommend(horizon, top_n=100, scan_limit=SCAN)
        match = next((r for r in recs if r.symbol == symbol), None)
        if match is None:
            raise HTTPException(404, f"{symbol} 추천 없음(유니버스 제외/스캔 밖/전체 abstain)")
        rec_dict = asdict(match)
        explanation = explain_mod.explain(rec_dict, client=ctx.llm_client,
                                          model=ctx.config.llm_model)
        return {"recommendation": rec_dict, "explanation": explanation}

    @app.get("/api/diagnostics/{symbol}/{horizon}")
    def diagnostics(symbol: str, horizon: str) -> dict:
        if horizon not in HORIZONS:
            raise HTTPException(404, f"unknown horizon: {horizon}")
        return ctx.service.diagnose(horizon, symbol)

    @app.get("/api/oscillator/{symbol}")
    def oscillator(symbol: str, days: int = Query(60, ge=10, le=250)) -> dict:
        if _md_blocked():
            return {**_MARKETDATA_DISABLED, "symbol": symbol}
        """종목별 오실레이터 상세 — 종목 일봉 기반.
        RSI(14) + 스토캐스틱 %K(14)/%D(3) + (수급 있으면) 외국인 누적 RSI.
        실제 증권사 값과 맞도록 전체 일봉으로 계산한 뒤 최근 구간만 반환(RSI Wilder 워밍업)."""
        o = ctx.ssot.get(symbol, Kind.OHLCV.value)
        if not o or not o.payload.get("bars"):
            raise HTTPException(404, f"일봉 데이터 없음: {symbol}")
        all_bars = o.payload["bars"]                      # 전체(계산용)
        a_dates = [b["date"] for b in all_bars]
        a_close = [float(b["c"]) for b in all_bars]
        a_high = [float(b.get("h", b["c"])) for b in all_bars]
        a_low = [float(b.get("l", b["c"])) for b in all_bars]
        N = len(a_close)
        # RSI(14) — 전체로 계산(Wilder)
        a_rsi = _rsi_series(a_close, period=14)
        # 스토캐스틱 Fast %K(14), %D = %K 3SMA
        kp = 14
        a_k: list = [None] * N
        for i in range(N):
            if i < kp - 1:
                continue
            wh = max(a_high[i - kp + 1:i + 1]); wl = min(a_low[i - kp + 1:i + 1])
            a_k[i] = round((a_close[i] - wl) / (wh - wl) * 100, 1) if wh > wl else 50.0
        a_d: list = [None] * N
        for i in range(N):
            seg = [a_k[j] for j in range(max(0, i - 2), i + 1) if a_k[j] is not None]
            if len(seg) == 3:
                a_d[i] = round(sum(seg) / 3, 1)
        # 표시 구간(최근 days)
        s = max(0, N - days)
        dates, closes = a_dates[s:], a_close[s:]
        rsi, raw_k, stoch_d = a_rsi[s:], a_k[s:], a_d[s:]
        # 수급 오실레이터(외국인 누적 RSI) — 전체로 계산 후 절단
        supply_osc: list = []
        sdp = ctx.ssot.get(symbol, Kind.SUPPLY.value)
        if sdp:
            daily = sdp.payload.get("daily", []) or []
            cum, run = [], 0.0
            for d in daily:
                run += d.get("foreign_net") or 0.0
                cum.append(round(run, 1))
            if len(cum) >= 3:
                full = _rsi_series(cum, period=min(14, max(2, len(cum) - 1)))
                supply_osc = full[max(0, len(full) - days):]
        def _last(arr):
            for v in reversed(arr):
                if v is not None:
                    return v
            return None
        # MACD(12,26,9) — 전체로 계산 후 절단
        def _ema(vals, span):
            k = 2.0 / (span + 1)
            out = [None] * len(vals)
            ema = None
            for i, v in enumerate(vals):
                ema = v if ema is None else (v * k + ema * (1 - k))
                out[i] = round(ema, 2)
            return out
        ema12 = _ema(a_close, 12); ema26 = _ema(a_close, 26)
        macd_line = [round(ema12[i] - ema26[i], 2) for i in range(N)]
        signal_line = _ema(macd_line, 9)
        hist = [round(macd_line[i] - signal_line[i], 2) for i in range(N)]
        macd = macd_line[s:]; macd_sig = signal_line[s:]; macd_hist = hist[s:]
        # 신호 판정(최근)
        signals = []
        lr = _last(rsi)
        if lr is not None:
            if lr >= 70: signals.append({"k": "RSI", "t": "과매수", "lv": "warn"})
            elif lr <= 30: signals.append({"k": "RSI", "t": "과매도", "lv": "buy"})
        lk, ld = _last(raw_k), _last(stoch_d)
        if lk is not None and ld is not None:
            if lk >= 80 and ld >= 80: signals.append({"k": "스토캐스틱", "t": "과매수권", "lv": "warn"})
            elif lk <= 20 and ld <= 20: signals.append({"k": "스토캐스틱", "t": "과매도권", "lv": "buy"})
        # MACD 골든/데드 크로스(최근 2틱)
        if len(macd) >= 2 and macd[-1] is not None and macd_sig[-1] is not None and macd[-2] is not None and macd_sig[-2] is not None:
            prev_d = macd[-2] - macd_sig[-2]; cur_d = macd[-1] - macd_sig[-1]
            if prev_d <= 0 < cur_d: signals.append({"k": "MACD", "t": "골든크로스", "lv": "buy"})
            elif prev_d >= 0 > cur_d: signals.append({"k": "MACD", "t": "데드크로스", "lv": "warn"})
        # 종합 판정 — 여러 지표를 점수화(0=과매도, 100=과매수)
        scores = []
        if lr is not None:
            scores.append(lr)                                  # RSI 0~100
        if lk is not None and ld is not None:
            scores.append((lk + ld) / 2)                       # 스토캐스틱 0~100
        ls = _last(supply_osc)
        if ls is not None:
            scores.append(ls)                                  # 수급 RSI
        gauge = round(sum(scores) / len(scores), 1) if scores else None
        if gauge is None:
            verdict = {"label": "데이터 부족", "lv": "neu",
                       "desc": "지표 계산에 필요한 일봉이 아직 부족합니다."}
        elif gauge >= 75:
            verdict = {"label": "과열 구간", "lv": "warn",
                       "desc": "단기 과매수입니다. 추격 매수보다 조정 가능성에 유의하세요."}
        elif gauge >= 60:
            verdict = {"label": "강세 우위", "lv": "up",
                       "desc": "상승 쪽으로 기운 상태입니다. 과열 전환 여부를 지켜보세요."}
        elif gauge > 40:
            verdict = {"label": "중립", "lv": "neu",
                       "desc": "뚜렷한 과매수·과매도가 아닙니다. 방향성 신호를 기다리는 구간."}
        elif gauge > 25:
            verdict = {"label": "약세 우위", "lv": "down",
                       "desc": "하락 쪽으로 기운 상태입니다. 반등 신호 확인 전 신중."}
        else:
            verdict = {"label": "침체 구간", "lv": "buy",
                       "desc": "단기 과매도입니다. 낙폭과대 반등 가능성을 참고하세요(반등 보장 아님)."}
        # 이동평균선(5/20/60) — 표시 구간
        def _sma_series(vals, w):
            out = [None] * len(vals)
            for i in range(len(vals)):
                if i >= w - 1:
                    out[i] = round(sum(vals[i - w + 1:i + 1]) / w, 1)
            return out
        ma5_full = _sma_series(a_close, 5); ma20_full = _sma_series(a_close, 20); ma60_full = _sma_series(a_close, 60)
        ma5, ma20, ma60 = ma5_full[s:], ma20_full[s:], ma60_full[s:]
        # 정배열/역배열 판정(최근)
        m5, m20, m60 = _last(ma5), _last(ma20), _last(ma60)
        ma_align = "혼조"
        if m5 is not None and m20 is not None and m60 is not None:
            if m5 > m20 > m60: ma_align = "정배열"      # 강세
            elif m5 < m20 < m60: ma_align = "역배열"    # 약세
        # 볼린저밴드(20, 2σ)
        bb_w = 20
        bb_mid_full = _sma_series(a_close, bb_w)
        bb_up_full: list = [None] * N; bb_low_full: list = [None] * N
        for i in range(N):
            if i >= bb_w - 1 and bb_mid_full[i] is not None:
                seg = a_close[i - bb_w + 1:i + 1]
                mean = bb_mid_full[i]
                sd = (sum((x - mean) ** 2 for x in seg) / bb_w) ** 0.5
                bb_up_full[i] = round(mean + 2 * sd, 1)
                bb_low_full[i] = round(mean - 2 * sd, 1)
        bb_mid, bb_up, bb_low = bb_mid_full[s:], bb_up_full[s:], bb_low_full[s:]
        # %B(밴드 내 위치 0~1) — 현재가가 밴드 어디쯤인지
        bb_pos = None
        if bb_up[-1] is not None and bb_low[-1] is not None and bb_up[-1] > bb_low[-1]:
            bb_pos = round((closes[-1] - bb_low[-1]) / (bb_up[-1] - bb_low[-1]) * 100, 1)
        # OBV(누적 거래량 균형)
        obv_full: list = [0.0] * N
        bars_v = [b.get("v", 0) for b in all_bars]
        for i in range(1, N):
            if a_close[i] > a_close[i - 1]:
                obv_full[i] = obv_full[i - 1] + bars_v[i]
            elif a_close[i] < a_close[i - 1]:
                obv_full[i] = obv_full[i - 1] - bars_v[i]
            else:
                obv_full[i] = obv_full[i - 1]
        obv = [round(x) for x in obv_full[s:]]
        # 다이버전스 감지 — 최근 구간에서 주가 고점은 높아지는데 RSI 고점은 낮아지면 약세 다이버전스
        divergence = None
        try:
            win = min(20, len(closes))
            cseg = closes[-win:]; rseg = rsi[-win:]
            half = win // 2
            if half >= 3:
                c1, c2 = max(cseg[:half]), max(cseg[half:])
                r1 = max(x for x in rseg[:half] if x is not None) if any(x is not None for x in rseg[:half]) else None
                r2 = max(x for x in rseg[half:] if x is not None) if any(x is not None for x in rseg[half:]) else None
                cl1, cl2 = min(cseg[:half]), min(cseg[half:])
                rl1 = min(x for x in rseg[:half] if x is not None) if any(x is not None for x in rseg[:half]) else None
                rl2 = min(x for x in rseg[half:] if x is not None) if any(x is not None for x in rseg[half:]) else None
                if r1 is not None and r2 is not None and c2 > c1 and r2 < r1 - 2:
                    divergence = {"type": "bearish", "label": "약세 다이버전스",
                                  "desc": "주가는 고점을 높이는데 RSI는 낮아짐 — 상승 동력 약화 신호."}
                elif rl1 is not None and rl2 is not None and cl2 < cl1 and rl2 > rl1 + 2:
                    divergence = {"type": "bullish", "label": "강세 다이버전스",
                                  "desc": "주가는 저점을 낮추는데 RSI는 높아짐 — 하락 동력 약화 신호."}
        except Exception:
            divergence = None
        if divergence:
            signals.append({"k": "다이버전스", "t": divergence["label"],
                            "lv": "warn" if divergence["type"] == "bearish" else "buy"})
        return {"symbol": symbol, "name": ctx.name_of(symbol),
                "market": ctx.service._market(symbol),
                "dates": dates, "closes": closes,
                "rsi": rsi, "stoch_k": raw_k, "stoch_d": stoch_d,
                "macd": macd, "macd_signal": macd_sig, "macd_hist": macd_hist,
                "ma5": ma5, "ma20": ma20, "ma60": ma60, "ma_align": ma_align,
                "bb_up": bb_up, "bb_mid": bb_mid, "bb_low": bb_low, "bb_pos": bb_pos,
                "obv": obv, "divergence": divergence,
                "supply_osc": supply_osc, "bars_total": N, "signals": signals,
                "gauge": gauge, "verdict": verdict,
                "last": {"close": closes[-1] if closes else None,
                         "rsi": _last(rsi), "stoch_k": _last(raw_k),
                         "stoch_d": _last(stoch_d), "supply_osc": _last(supply_osc),
                         "macd": _last(macd), "macd_signal": _last(macd_sig),
                         "ma5": m5, "ma20": m20, "ma60": m60, "bb_pos": bb_pos}}

    @app.get("/api/realtime_supply/{symbol}")
    def realtime_supply(symbol: str) -> dict:
        if _md_blocked():
            return {**_MARKETDATA_DISABLED, "symbol": symbol}
        """실시간 체결 수급 압력 — 체결강도 + 호가잔량 불균형 기반.
        ⚠ 외국인/기관 '실시간 순매수'가 아님(그건 장 마감 집계). 리테일에서 받을 수 있는
        실시간 매수/매도 압력(체결강도·호가잔량)을 보여준다."""
        now = ctx.clock.now()
        out: dict = {"symbol": symbol, "name": ctx.name_of(symbol),
                     "ts": now.isoformat(), "market_hours": False}
        try:
            from app.core.clock import is_market_hours as _imh
            out["market_hours"] = _imh(now)
        except Exception:
            pass
        if ctx.config.data_source == "mock":
            # mock: tick의 strength + orderbook로 데모
            tick = ctx.provider.fetch(symbol, Kind.TICK.value, now=now)
            ob = ctx.provider.fetch(symbol, Kind.ORDERBOOK.value, now=now)
            if tick:
                out["strength"] = tick.payload.get("strength")
            if ob:
                bids = ob.payload.get("bids", []); asks = ob.payload.get("asks", [])
                bq = sum(b[1] for b in bids); aq = sum(a[1] for a in asks)
                out["total_bid_qty"] = bq; out["total_ask_qty"] = aq
            out["demo"] = True
        else:
            # 1) 체결강도 — current_price(cttr)
            if ctx.quote is not None:
                try:
                    q = ctx.quote.current_price(symbol)
                    out["strength"] = q.get("strength")
                    out["price"] = q.get("price"); out["change_pct"] = q.get("change_pct")
                    out["volume"] = q.get("volume"); out["turnover"] = q.get("turnover")
                except Exception as e:
                    out["error"] = str(e)
                # 2) 호가잔량 불균형 — asking_price(총매수/매도 잔량)
                try:
                    ap = ctx.quote.asking_price(symbol)
                    if ap:
                        out["total_bid_qty"] = ap.get("total_bid_qty")
                        out["total_ask_qty"] = ap.get("total_ask_qty")
                        out["bids"] = ap.get("bids"); out["asks"] = ap.get("asks")
                except Exception:
                    pass
            # WS 호가가 더 신선하면 그것으로 덮어쓰기(구독 종목)
            ob = ctx.ssot.get(symbol, Kind.ORDERBOOK.value)
            if ob and ob.payload.get("bids"):
                out["bids"] = out.get("bids") or ob.payload.get("bids")
                out["asks"] = out.get("asks") or ob.payload.get("asks")
        # 압력 점수 계산: 체결강도(100기준) + 호가잔량 불균형
        pressure = None; parts = []
        s = out.get("strength")
        if s is not None:
            # 체결강도 100 → 중립. 100보다 크면 매수우위. 0~200을 -100~+100으로
            parts.append(max(-100, min(100, (s - 100))))
        bq = out.get("total_bid_qty"); aq = out.get("total_ask_qty")
        if bq and aq and (bq + aq) > 0:
            imbalance = (bq - aq) / (bq + aq) * 100   # +면 매수잔량 우위
            out["orderbook_imbalance"] = round(imbalance, 1)
            parts.append(max(-100, min(100, imbalance)))
        if parts:
            pressure = round(sum(parts) / len(parts), 1)
        out["pressure"] = pressure     # -100(매도우위) ~ +100(매수우위)
        if pressure is not None:
            out["pressure_label"] = ("강한 매수세" if pressure >= 40 else
                                     "매수 우위" if pressure >= 12 else
                                     "강한 매도세" if pressure <= -40 else
                                     "매도 우위" if pressure <= -12 else "중립")
        out["note"] = "체결강도·호가잔량 기반 실시간 매수/매도 압력입니다(외국인·기관 실시간 순매수 아님)."
        return out

    @app.get("/api/supply_detail/{symbol}")
    def supply_detail(symbol: str, days: int = Query(5, ge=1, le=20)) -> dict:
        if _md_blocked():
            return {**_MARKETDATA_DISABLED, "symbol": symbol}
        """종목별 세부 투자주체 수급(연기금·투신·사모 등) — KIS 응답에 세부 필드가 있을 때.
        없으면 외국인/기관/개인 기본 3주체만 반환."""
        sup = ctx.ssot.get(symbol, Kind.SUPPLY.value)
        if sup is None and ctx.config.data_source != "mock":
            try:
                ctx.service.refresh_data([symbol], [Kind.SUPPLY.value])
                sup = ctx.ssot.get(symbol, Kind.SUPPLY.value)
            except Exception:
                pass
        out: dict = {"symbol": symbol, "name": ctx.name_of(symbol), "has_detail": False}
        if not sup:
            out["note"] = "수급 데이터가 아직 적재되지 않았습니다."
            return out
        daily = sup.payload.get("daily", [])[-days:]
        # 기본 3주체 누적
        agg = {"foreign": 0.0, "inst": 0.0, "retail": 0.0}
        sub_agg: dict = {}
        try:
            from app.providers.kis import _SUB_KO
        except Exception:
            _SUB_KO = {}
        for d in daily:
            agg["foreign"] += d.get("foreign_net") or 0
            agg["inst"] += d.get("inst_net") or 0
            agg["retail"] += d.get("retail_net") or 0
            for k, v in (d.get("sub") or {}).items():
                sub_agg[k] = sub_agg.get(k, 0.0) + (v or 0)
        out["days"] = len(daily)
        out["main"] = [
            {"key": "foreign", "label": "외국인", "net": round(agg["foreign"], 1)},
            {"key": "inst", "label": "기관", "net": round(agg["inst"], 1)},
            {"key": "retail", "label": "개인", "net": round(agg["retail"], 1)},
        ]
        if sub_agg:
            out["has_detail"] = True
            out["source"] = "kis"
            out["sub"] = [{"key": k, "label": _SUB_KO.get(k, k), "net": round(v, 1)}
                          for k, v in sorted(sub_agg.items(), key=lambda x: -abs(x[1]))]
            return out
        # KIS 응답에 세부가 없으면 KRX 개별종목 투자자별 거래실적에서 시도(연기금·투신·사모)
        if ctx.config.data_source != "mock":
            try:
                from app.providers.krx import fetch_investor_detail
                krx_sub = fetch_investor_detail(symbol, days=days)
                if krx_sub:
                    # 기관 세부만(외국인/개인 제외) 추려서 표시
                    inst_keys = ("pension", "trust", "private", "bank", "insurance",
                                 "fin_invest", "other_fin", "other_corp")
                    sub_list = [{"key": k, "label": _SUB_KO.get(k, k), "net": round(v, 1)}
                                for k, v in krx_sub.items() if k in inst_keys]
                    if sub_list:
                        out["has_detail"] = True
                        out["source"] = "krx"
                        out["sub"] = sorted(sub_list, key=lambda x: -abs(x["net"]))
                        return out
            except Exception:
                pass
        out["note"] = ("이 종목·환경에서는 세부 투자주체(연기금·투신 등) 데이터를 가져오지 못했습니다. "
                       "KIS 기본 응답엔 세부가 없고, KRX 직접 조회도 실패했습니다(방화벽 가능). 기관 합계만 표시합니다.")
        return out

    @app.get("/api/watchlist")
    def watchlist_get() -> dict:
        """관심종목 목록 + 각 종목의 현재 수급 스냅샷."""
        import json as _json
        row = ctx.service.store.get_setting("user_watchlist")
        syms = []
        if row:
            try:
                syms = _json.loads(row["value"])
            except Exception:
                syms = []
        items = []
        for s in syms:
            o = ctx.ssot.get(s, "ohlcv")
            net_f = ctx.service._net_buy(s, "foreign", days=1)
            net_i = ctx.service._net_buy(s, "inst", days=1)
            streak_f = ctx.service._net_buy_streak(s, "foreign")
            items.append({
                "symbol": s, "name": ctx.name_of(s),
                "market": ctx.service._market(s),
                "change_pct": ctx.service._change_pct(s),
                "ref_price": _ref_price(ctx.ssot, s),
                "net_foreign": net_f, "net_inst": net_i,
                "streak_foreign": streak_f,
                "turnover": ctx.service._turnover(s),
            })
        return {"symbols": syms, "items": items}

    @app.post("/api/watchlist")
    def watchlist_set(body: dict = Body(...)) -> dict:
        """관심종목 추가/삭제. body={action:'add'|'remove'|'set', symbol|symbols}."""
        import json as _json
        row = ctx.service.store.get_setting("user_watchlist")
        cur = []
        if row:
            try:
                cur = _json.loads(row["value"])
            except Exception:
                cur = []
        action = body.get("action", "add")
        if action == "set":
            cur = [str(s) for s in (body.get("symbols") or [])][:100]
        elif action == "add":
            s = str(body.get("symbol", "")).strip()
            if s and s not in cur:
                cur.append(s)
        elif action == "remove":
            s = str(body.get("symbol", "")).strip()
            cur = [x for x in cur if x != s]
        ctx.service.store.set_setting("user_watchlist", _json.dumps(cur),
                                      ctx.clock.now().isoformat())
        return {"symbols": cur, "ok": True}

    @app.get("/api/watchlist_alerts")
    def watchlist_alerts() -> dict:
        """관심종목 수급 변화 알림 — 외국인/기관 순매수 전환·연속·강도·신고가 등 신호."""
        import json as _json
        row = ctx.service.store.get_setting("user_watchlist")
        syms = []
        if row:
            try:
                syms = _json.loads(row["value"])
            except Exception:
                syms = []
        alerts = []
        for s in syms:
            nm = ctx.name_of(s)
            net_f = ctx.service._net_buy(s, "foreign", days=1)
            net_i = ctx.service._net_buy(s, "inst", days=1)
            streak_f = ctx.service._net_buy_streak(s, "foreign")
            streak_i = ctx.service._net_buy_streak(s, "inst")
            if streak_f >= 3:
                alerts.append({"symbol": s, "name": nm, "level": "buy",
                               "msg": f"외국인 {streak_f}일 연속 순매수"})
            if streak_i >= 3:
                alerts.append({"symbol": s, "name": nm, "level": "buy",
                               "msg": f"기관 {streak_i}일 연속 순매수"})
            if net_f is not None and net_i is not None and net_f > 0 and net_i > 0:
                alerts.append({"symbol": s, "name": nm, "level": "buy",
                               "msg": "외국인·기관 동반 순매수(쌍끌이)"})
            if ctx.service._is_near_high(s):
                alerts.append({"symbol": s, "name": nm, "level": "info",
                               "msg": "60일 신고가 부근"})
        return {"alerts": alerts, "count": len(alerts)}

    @app.get("/api/search_diag")
    def search_diag(q: str = "") -> dict:
        """전종목 검색 진단 — 유니버스 크기, 매칭 수, KRX/DART 로드 상태, 샘플.
        사용자가 '왜 검색이 안 되는지' 직접 확인하는 용도."""
        uni = list(ctx.search_universe or ctx.universe)
        import os as _os
        # corp_map 실제 종목 수(있음/없음만으로는 부족 — 빈 {} 구분)
        _cm_count = 0
        try:
            if _os.path.exists("dart_corp_map.json"):
                import json as _json
                with open("dart_corp_map.json", encoding="utf-8") as f:
                    _cm_count = len(_json.load(f))
        except Exception:
            _cm_count = 0
        diag: dict = {
            "version": BUILD_VERSION,
            "universe_size": len(uni),
            "universe_mode": getattr(ctx.config, "universe_mode", "?"),
            "loaded_ohlcv": len(ctx.ssot.symbols()),
            "krx_cache_exists": _os.path.exists("krx_stocks.json"),
            "dart_map_exists": _os.path.exists("dart_corp_map.json"),
            "dart_map_count": _cm_count,
            "dart_names_exists": _os.path.exists("dart_corp_names.json"),
            "dart_key_set": bool(getattr(ctx.config, "dart_api_key", "")),
            "stocks_csv_exists": _os.path.exists("stocks.csv") or _os.path.exists("krx_stocks.csv"),
            "data_source": ctx.config.data_source,
            "cwd": _os.getcwd(),
        }
        # CORPCODE 파일 탐색 결과(사용자가 파일 인식 여부 확인용)
        try:
            import glob as _g
            cc_files = []
            for d in (_os.getcwd(), "data"):
                if _os.path.isdir(d):
                    for f in _g.glob(_os.path.join(d, "*.xml")) + _g.glob(_os.path.join(d, "*.zip")):
                        if "corpcode" in _os.path.basename(f).lower():
                            cc_files.append(_os.path.basename(f))
            diag["corpcode_files_found"] = cc_files
        except Exception:
            diag["corpcode_files_found"] = []
        # KRX 캐시 종목 수
        if diag["krx_cache_exists"]:
            try:
                import json as _json
                with open("krx_stocks.json", encoding="utf-8") as f:
                    diag["krx_cache_count"] = len(_json.load(f).get("rows", []))
            except Exception:
                diag["krx_cache_count"] = 0
        # 검색 시뮬레이션
        if q:
            ql = q.strip().lower().replace(" ", "")
            matched = []
            for s in uni:
                nm = (ctx.name_of(s) or "").lower().replace(" ", "")
                if ql in s.lower() or ql in nm:
                    matched.append({"symbol": s, "name": ctx.name_of(s),
                                    "market": ctx.service._market(s)})
                if len(matched) >= 30:
                    break
            diag["query"] = q
            diag["match_count"] = len(matched)
            diag["matched_sample"] = matched[:15]
        # 유니버스 샘플(이름 해석 확인)
        diag["universe_sample"] = [{"symbol": s, "name": ctx.name_of(s),
                                    "market": ctx.service._market(s)} for s in uni[:10]]
        # 진단 해석
        msgs = []
        if diag["universe_size"] < 300:
            msgs.append(f"⚠ 검색 가능 종목이 {diag['universe_size']}개뿐입니다. 전종목 로드가 실패했습니다.")
            if diag["dart_map_exists"] and diag["dart_map_count"] < 300:
                msgs.append(f"→ dart_corp_map.json 파일은 있지만 종목이 {diag['dart_map_count']}개뿐입니다(비정상). "
                            "DART corpCode 다운로드가 실패했습니다. 서버 콘솔의 [corp_map] 오류 메시지를 확인하세요.")
                msgs.append("→ dart_corp_map.json, dart_corp_names.json 파일을 삭제하고 재시작하면 다시 생성을 시도합니다.")
            if diag.get("corpcode_files_found"):
                msgs.append(f"→ CORPCODE 파일을 찾았습니다({', '.join(diag['corpcode_files_found'])}). "
                            "그런데도 종목이 적다면 파일이 손상됐거나 형식이 다릅니다. 서버 콘솔의 [corp_map] 메시지를 확인하세요.")
            else:
                msgs.append(f"→ 현재 작업폴더({diag['cwd']})에 CORPCODE.xml 이 보이지 않습니다. "
                            "이 폴더에 정확히 두었는지 확인하세요(다른 폴더면 인식 안 됩니다).")
            if not diag["dart_key_set"]:
                msgs.append("→ DART_API_KEY를 .env에 설정하면 전종목 맵을 자동 생성합니다(가장 안정적).")
            if not diag["stocks_csv_exists"]:
                msgs.append("→ 가장 확실한 방법: KRX에서 전종목 CSV를 받아 stocks.csv로 저장 후 재시작(방화벽 무관). "
                            "자세히는 전종목검색_안내.txt 참고.")
        else:
            msgs.append(f"✓ {diag['universe_size']}개 종목이 검색 대상입니다.")
        diag["diagnosis"] = msgs
        return diag

    @app.get("/api/monitor")
    def monitor() -> dict:
        """런타임 모니터링 — 에러 카운트 + 최근 로그 꼬리(운영 점검용)."""
        out: dict = {"version": BUILD_VERSION, "data_source": ctx.config.data_source,
                     "universe_size": len(ctx.universe), "ssot_size": ctx.ssot.size()}
        try:
            out["errors"] = ctx.errors.snapshot() if ctx.errors else {"total": 0}
        except Exception:
            out["errors"] = {"total": 0}
        # 최근 로그 30줄
        try:
            import os as _os
            lp = _os.path.join("logs", "app.log")
            if _os.path.exists(lp):
                with open(lp, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                out["log_tail"] = [ln.rstrip() for ln in lines[-30:]]
        except Exception:
            out["log_tail"] = []
        return out

    @app.get("/api/supply_backtest")
    def supply_backtest(mode: str = "foreign", top_n: int = Query(10, ge=3, le=30),
                        hold_days: int = Query(5, ge=1, le=20)) -> dict:
        if _md_blocked():
            return {**_MARKETDATA_DISABLED}
        """수급 점수 검증 — 과거 각 시점에서 수급 상위 종목을 골랐을 때
        hold_days 후 실제 수익률을 추적(점수 품질의 증거). 적재된 종목 일봉 기준."""
        if mode not in ("foreign", "inst"):
            raise HTTPException(404, "mode는 foreign|inst")
        who = mode
        syms = ctx.ssot.symbols()
        # 각 종목의 (일자별 수급 누적, 일봉) 으로 간이 백테스트
        results = []
        sample = []
        for sym in syms:
            o = ctx.ssot.get(sym, Kind.OHLCV.value)
            sup = ctx.ssot.get(sym, Kind.SUPPLY.value)
            if not o or not sup:
                continue
            bars = o.payload.get("bars", [])
            daily = sup.payload.get("daily", [])
            if len(bars) < hold_days + 6 or len(daily) < 6:
                continue
            # 최근 평가 시점: 마지막에서 hold_days 이전
            idx = len(bars) - 1 - hold_days
            if idx < 5:
                continue
            # 그 시점 직전 5일 순매수 합(수급 강도 대용)
            field = "foreign_net" if who == "foreign" else "inst_net"
            recent_net = sum((d.get(field) or 0) for d in daily[-(hold_days + 5):-hold_days])
            entry = bars[idx]["c"]; exit_ = bars[idx + hold_days]["c"]
            if not entry:
                continue
            ret = (exit_ - entry) / entry * 100
            sample.append({"symbol": sym, "name": ctx.name_of(sym),
                           "net5": round(recent_net, 1), "ret": round(ret, 2)})
        if not sample:
            return {"mode": mode, "hold_days": hold_days, "note": "검증할 적재 데이터가 부족합니다(수급+일봉).",
                    "picked": [], "avg_return": None, "baseline": None}
        # 수급 상위 top_n vs 전체 평균(베이스라인)
        sample.sort(key=lambda x: x["net5"], reverse=True)
        picked = sample[:top_n]
        avg_picked = sum(x["ret"] for x in picked) / len(picked)
        avg_all = sum(x["ret"] for x in sample) / len(sample)
        winners = sum(1 for x in picked if x["ret"] > 0)
        return {"mode": mode, "hold_days": hold_days, "universe_tested": len(sample),
                "picked": picked,
                "avg_return": round(avg_picked, 2),
                "baseline": round(avg_all, 2),
                "edge": round(avg_picked - avg_all, 2),
                "win_rate": round(winners / len(picked) * 100, 1),
                "note": "수급 상위 종목의 사후 수익률이 베이스라인(전체 평균)보다 높으면 수급 점수에 예측력이 있다는 신호입니다. 과거 성과는 미래를 보장하지 않습니다."}

    @app.get("/api/stock/{symbol}")
    def stock(symbol: str, refresh: bool = True) -> dict:
        # 실시간 공시: live 모드면 이 종목의 뉴스(DART 공시)를 즉시 재적재 후 반환.
        now = ctx.clock.now()
        refreshed = False
        if refresh and ctx.config.data_source != "mock":
            try:
                ctx.service.refresh_data([symbol], [Kind.NEWS.value])
                refreshed = True
            except Exception:
                refreshed = False
        g = ctx.ssot.get
        out: dict = {"symbol": symbol, "name": ctx.name_of(symbol), "news_refreshed": refreshed}
        o = g(symbol, Kind.OHLCV.value)
        if o and o.payload.get("bars"):
            bars = o.payload["bars"]; last = bars[-1]
            prev = bars[-2]["c"] if len(bars) >= 2 else last["c"]
            price = {"close": last["c"],
                     "change_pct": round((last["c"] - prev) / prev * 100, 2) if prev else 0.0,
                     "volume": last["v"], "status": o.payload.get("status")}
            # 52주(거래일 ~252) 신고가/신저가 + 현재 위치(%)
            window = bars[-252:] if len(bars) > 252 else bars
            hi = max(b["h"] for b in window)
            lo = min(b["l"] for b in window)
            price["high_52w"] = hi
            price["low_52w"] = lo
            if hi > lo:
                price["pos_52w"] = round((last["c"] - lo) / (hi - lo) * 100, 1)  # 0=저점,100=고점
            price["off_high_pct"] = round((last["c"] - hi) / hi * 100, 1) if hi else None  # 고점대비(-)
            price["weeks_covered"] = round(len(window) / 5)
            out["price"] = price
        n = g(symbol, Kind.NEWS.value)
        disclosures = (n.payload.get("items") if n else []) or []
        for it in disclosures:
            it.setdefault("source", "공시")
        # 실시간 언론 기사(네이버+구글) 병합
        press = []
        if _press_on():
            try:
                press = _fetch_press(out["name"], now)
            except Exception:
                press = []
        merged = disclosures + press
        merged.sort(key=lambda x: x.get("published_at", ""), reverse=True)  # 최신순
        out["news"] = merged
        out["data_source"] = ctx.config.data_source          # mock=데모 / live=실제
        out["has_press"] = bool(press)                        # 네이버 실기사 포함 여부
        out["press_enabled"] = (ctx.press_news is not None and getattr(ctx.press_news, "enabled", False))
        if n is not None:
            out["news_as_of"] = n.fetched_at.isoformat()
        f = g(symbol, Kind.FINANCIALS.value)
        if f:
            p = f.payload
            fin = {k: p.get(k) for k in ("revenue", "op_income", "net_income",
                                         "debt_ratio", "revenue_yoy", "op_yoy",
                                         "per", "pbr", "bsns_year")}
            fin["as_of"] = f.as_of.isoformat()
            out["financials"] = fin
        s = g(symbol, Kind.SUPPLY.value)
        if s and s.payload.get("daily"):
            d = s.payload["daily"][-5:]
            out["supply"] = {"foreign_5d": round(sum(x["foreign_net"] for x in d), 1),
                             "inst_5d": round(sum(x["inst_net"] for x in d), 1)}
        sh = g(symbol, Kind.SHORT.value)
        if sh:
            out["short"] = {"ratio": sh.payload.get("short_balance_ratio"),
                            "trend": sh.payload.get("trend")}
        return out

    @app.get("/api/realtime/{symbol}")
    def realtime(symbol: str) -> dict:
        if _md_blocked():
            return {**_MARKETDATA_DISABLED, "symbol": symbol}
        now = ctx.clock.now()
        out: dict = {"symbol": symbol, "name": ctx.name_of(symbol), "ts": now.isoformat()}
        if ctx.config.data_source == "mock":
            # mock 실시간: now 기반 tick/orderbook (호출마다 변동 -> 실시간 느낌)
            tick = ctx.provider.fetch(symbol, Kind.TICK.value, now=now)
            ob = ctx.provider.fetch(symbol, Kind.ORDERBOOK.value, now=now)
            if tick:
                out["price"] = tick.payload.get("price")
                out["strength"] = tick.payload.get("strength")
            o = ctx.ssot.get(symbol, Kind.OHLCV.value)
            if o and o.payload.get("bars") and out.get("price"):
                bars = o.payload["bars"]
                prev = bars[-2]["c"] if len(bars) >= 2 else bars[-1]["c"]   # 전일 종가
                out["change_pct"] = round((out["price"] - prev) / prev * 100, 2) if prev else 0.0
                out["prev_close"] = prev
            if ob:
                out["orderbook"] = {"bids": ob.payload.get("bids"), "asks": ob.payload.get("asks")}
        else:
            if ctx.quote is not None:
                try:
                    q = ctx.quote.current_price(symbol)
                    out.update({"price": q["price"], "change": q["change"],
                                "change_pct": q["change_pct"], "volume": q["volume"],
                                "turnover": q.get("turnover")})
                    # 시장 분류(코스피/코스닥) + 실시간 거래량·거래대금을 OHLCV 최신봉에 반영
                    o = ctx.ssot.get(symbol, Kind.OHLCV.value)
                    if o and o.payload.get("bars"):
                        if q.get("market"):
                            o.payload["market"] = q["market"]
                        lb = o.payload["bars"][-1]
                        if q.get("volume"):
                            lb["v"] = int(q["volume"])
                        if q.get("turnover"):
                            lb["to"] = float(q["turnover"])
                except Exception as e:  # noqa: BLE001
                    out["error"] = str(e)
            else:
                out["error"] = "현재가 provider 없음 (RECO_DATA_SOURCE=live, KIS 키 확인)"
            ob = ctx.ssot.get(symbol, Kind.ORDERBOOK.value)   # WS 피드(구독 종목)
            if ob:
                out["orderbook"] = {"bids": ob.payload.get("bids"), "asks": ob.payload.get("asks")}
        return out

    # ===== 실시간 피드 =====
    # 공시: 워치리스트 종목별(DART). 뉴스/리포트: 시장 전체(일반 키워드) — 대형주 편중 해소.
    import time as _time
    _MARKET_NEWS_Q = [
        # 국내 시장 전반
        "증시", "코스피", "코스닥", "특징주", "급등주", "테마주", "상한가", "거래량 급증",
        # 국내 섹터(전 종목 폭넓게 커버)
        "반도체株", "2차전지", "바이오 제약", "자동차주", "조선주", "방산주",
        "금융주", "인터넷 게임", "엔터주", "원전 전력", "로봇 AI", "조선 해운",
        # 해외·세계 증시
        "미국증시", "나스닥", "S&P500", "글로벌 증시", "해외주식", "엔비디아",
        "테슬라", "연준 금리", "환율 원달러", "국제유가", "중국증시", "일본증시",
    ]
    _MARKET_REPORT_Q = [
        "목표주가 상향", "목표주가 하향", "투자의견 매수", "증권사 리포트",
        "신규 커버리지", "목표주가 신규", "어닝 서프라이즈 리포트", "실적 전망 리포트",
        "코스닥 목표주가", "중소형주 리포트", "강력매수 리포트", "목표가 상향",
        # 섹터 리포트(대형주 편중 해소)
        "반도체 리포트", "2차전지 리포트", "바이오 리포트", "자동차 리포트",
        "조선 리포트", "방산 리포트", "인터넷 리포트", "지주사 리포트",
    ]
    _feed = {"cursor": 0, "last_refresh": 0.0,            # 공시(종목별) 라운드로빈
             "news_cur": 0, "news_last": 0.0, "mkt_news": {},     # 시장 뉴스(키워드별)
             "rep_cur": 0, "rep_last": 0.0, "mkt_rep": {},        # 시장 리포트(키워드별)
             "mkt_disc": [], "mkt_disc_last": 0.0, "mkt_disc_err": None,    # 시장 전체 공시(DART)
             "mkt_disc_ok": 0.0,        # 마지막 '성공' 시각(epoch) — 갱신지연 판단용
             "news_ok": 0.0}            # 뉴스 마지막 성공 시각

    # 마지막 성공 데이터 디스크 영속(재시작에도 화면 안 비게) — settings 활용
    def _persist_feed(kind: str, items: list) -> None:
        try:
            ctx.store.set_setting("feedcache:" + kind,
                                  _json_mod.dumps(items[:60], ensure_ascii=False),
                                  ctx.clock.now().isoformat())
        except Exception:
            pass

    def _load_feed_cache(kind: str):
        try:
            row = ctx.store.get_setting("feedcache:" + kind)
            if row and row.get("value"):
                return _json_mod.loads(row["value"]), row.get("updated_at")
        except Exception:
            pass
        return None, None

    def _market_disc_refresh(interval: float = 30.0) -> None:
        """시장 전체 공시(DART list.json, corp_code 없이) 갱신. interval 초마다.
        주말/휴일이면 직전 거래일 공시를 잡도록 조회 기간을 늘린다."""
        if ctx.config.data_source == "mock" or ctx.dart is None or not ctx.config.dart_api_key:
            return
        now_t = _time.time()
        if _feed["mkt_disc"] and now_t - _feed["mkt_disc_last"] < interval:
            return
        _feed["mkt_disc_last"] = now_t
        try:
            now = ctx.clock.now()
            items = []
            # 2일 → 5일 → 10일로 점차 확대(주말+연휴 건너뛰어 최근 거래일 공시 확보)
            for d in (2, 5, 10):
                items = ctx.dart.recent_disclosures(now, days=d, max_pages=3)
                if items or ctx.dart.last_disclosure_error:
                    break
            for it in items:
                if not it.get("corp") and it.get("symbol"):
                    it["corp"] = ctx.name_of(it["symbol"])
                it["name"] = it.get("corp") or it.get("symbol") or ""
                it["link"] = it.get("url", "")      # UI 하이퍼링크용
            if items:
                _feed["mkt_disc"] = items
                _feed["mkt_disc_ok"] = now_t            # 성공 시각 기록
                _persist_feed("disc", items)            # 디스크 영속
            _feed["mkt_disc_err"] = ctx.dart.last_disclosure_error
        except Exception as e:
            _feed["mkt_disc_err"] = f"공시 조회 오류: {e}"
        # 메모리에 데이터가 없으면(첫 부팅 직후 실패 등) 디스크 캐시에서 복원
        if not _feed["mkt_disc"]:
            cached, ts = _load_feed_cache("disc")
            if cached:
                _feed["mkt_disc"] = cached

    def _press_on() -> bool:
        naver = ctx.press_news is not None and getattr(ctx.press_news, "enabled", False)
        google = ctx.google_news is not None and getattr(ctx.google_news, "enabled", False)
        return naver or google

    def _fetch_press(query: str, now) -> list:
        """네이버 + 구글 뉴스를 합쳐 가져온다(중복 제거). 한쪽이 죽어도 다른 쪽으로 동작."""
        items: list = []
        if ctx.press_news is not None and getattr(ctx.press_news, "enabled", False):
            try:
                items += ctx.press_news.fetch_news(query, now)
            except Exception:
                pass
        if ctx.google_news is not None and getattr(ctx.google_news, "enabled", False):
            try:
                items += ctx.google_news.fetch_news(query, now)
            except Exception:
                pass
        # 제목 기준 중복 제거(다른 포털이 같은 기사를 줄 수 있음)
        seen = set()
        out = []
        for it in items:
            key = "".join((it.get("title") or "").split())[:60]
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(it)
        # 최신순
        out.sort(key=lambda x: x.get("published_at") or "", reverse=True)
        return out

    def _disc_refresh_some(n: int = 4) -> None:
        """공시: 워치리스트 다음 n개 종목의 DART 공시를 SSOT 로 갱신(라운드로빈)."""
        syms = ctx.universe or []
        if not syms or ctx.config.data_source == "mock":
            return
        now_t = _time.time()
        if now_t - _feed["last_refresh"] < 3:
            return
        _feed["last_refresh"] = now_t
        for _ in range(min(n, len(syms))):
            i = _feed["cursor"] % len(syms)
            _feed["cursor"] = (i + 1) % len(syms)
            try:
                ctx.service.refresh_data([syms[i]], [Kind.NEWS.value])
            except Exception:
                pass

    def _market_news_refresh(n: int = 6) -> None:
        """뉴스: 일반 시장 키워드를 회전하며 네이버 기사 수집(시장 전체). 빠른 회전."""
        if not _press_on() or ctx.config.data_source == "mock":
            return
        now_t = _time.time()
        if now_t - _feed["news_last"] < 4:
            return
        _feed["news_last"] = now_t
        now = ctx.clock.now()
        got_any = False
        for _ in range(min(n, len(_MARKET_NEWS_Q))):
            i = _feed["news_cur"] % len(_MARKET_NEWS_Q)
            _feed["news_cur"] = (i + 1) % len(_MARKET_NEWS_Q)
            q = _MARKET_NEWS_Q[i]
            try:
                res = _fetch_press(q, now)
                _feed["mkt_news"][q] = res
                if res:
                    got_any = True
            except Exception:
                pass
        if got_any:
            _feed["news_ok"] = now_t
            # 영속(집계 결과 일부)
            try:
                flat = []
                for arts in _feed["mkt_news"].values():
                    flat.extend(arts or [])
                if flat:
                    _persist_feed("news", flat)
            except Exception:
                pass

    def _reports_refresh_some(n: int = 4) -> None:
        """리포트: 일반 목표주가/리포트 키워드를 회전(시장 전체, 대형주 편중 해소). 느린 주기."""
        if not _press_on() or ctx.config.data_source == "mock":
            return
        now_t = _time.time()
        if now_t - _feed["rep_last"] < 20:
            return
        _feed["rep_last"] = now_t
        now = ctx.clock.now()
        for _ in range(min(n, len(_MARKET_REPORT_Q))):
            i = _feed["rep_cur"] % len(_MARKET_REPORT_Q)
            _feed["rep_cur"] = (i + 1) % len(_MARKET_REPORT_Q)
            q = _MARKET_REPORT_Q[i]
            try:
                _feed["mkt_rep"][q] = [{**a, "topic": q} for a in _fetch_press(q, now)]
            except Exception:
                pass

    def _feed_aggregate(kind: str, limit: int) -> list:
        """kind='disclosure'(공시, 종목별) | 'news'(시장 뉴스, 키워드별)."""
        items = []
        if kind == "news":
            if ctx.config.data_source == "mock":
                for sym in (ctx.universe or [])[:80]:
                    name = ctx.name_of(sym)
                    nd = ctx.ssot.get(sym, Kind.NEWS.value)
                    for it in (nd.payload.get("items") if nd else []) or []:
                        if it.get("source") == "뉴스":
                            items.append({**it, "symbol": sym, "name": name})
            else:
                for q, arts in _feed["mkt_news"].items():
                    for a in arts:
                        items.append({**a, "topic": q})
        else:
            for sym in (ctx.universe or [])[:80]:
                name = ctx.name_of(sym)
                nd = ctx.ssot.get(sym, Kind.NEWS.value)
                for it in (nd.payload.get("items") if nd else []) or []:
                    if it.get("source", "공시") != "news":
                        items.append({**it, "symbol": sym, "name": name})
        items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        return _dedupe(items)[:limit]

    def _dedupe(items: list) -> list:
        """링크(우선) 또는 제목+종목 기준으로 중복 제거. 정렬 순서 유지(최신 우선)."""
        seen = set()
        out = []
        for it in items:
            link = (it.get("link") or "").strip()
            title = "".join((it.get("title") or "").split())   # 공백 정규화
            key = link or (it.get("symbol", "") + "|" + title)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(it)
        return out

    @app.get("/api/reports/{symbol}")
    def reports(symbol: str) -> dict:
        """증권사 리포트 동향. 공식 API가 없어 (1)네이버 뉴스 API로 '목표주가' 기사를 모으고
        (2)실제 리포트는 네이버 증권 리서치/검색 링크로 연결한다(본문 크롤링은 약관상 안 함)."""
        import urllib.parse
        now = ctx.clock.now()
        name = ctx.name_of(symbol)
        items = []
        enabled = ctx.press_news is not None and getattr(ctx.press_news, "enabled", False)
        if enabled:
            try:
                items = _fetch_press(name + " 목표주가", now)
            except Exception:
                items = []
        qc = urllib.parse.quote(name + " 목표주가 리포트")
        return {
            "symbol": symbol, "name": name, "items": items, "press_enabled": enabled,
            "links": [
                {"label": "네이버 증권 — 종목분석 리포트(애널리스트)",
                 "url": f"https://finance.naver.com/item/main.naver?code={symbol}"},
                {"label": "네이버 금융 리서치 — 종목 리포트 목록",
                 "url": f"https://finance.naver.com/research/company_list.naver?keyword=&searchType=itemCode&itemCode={symbol}"},
                {"label": "증권사 리포트·목표주가 검색",
                 "url": f"https://search.naver.com/search.naver?query={qc}"},
            ],
        }

    @app.get("/api/feed/disclosures")
    def feed_disclosures(limit: int = 40) -> dict:
        """시장 전체 실시간 공시 (DART, corp_code 없이 전 종목). 대형주 편중 해소."""
        if ctx.config.data_source == "mock":
            _disc_refresh_some(8)
            return {"items": _feed_aggregate("disclosure", limit),
                    "ts": ctx.clock.now().isoformat(), "data_source": "mock",
                    "scope": "demo", "universe": len(ctx.universe or [])}
        _market_disc_refresh(30)
        items = _dedupe(_feed["mkt_disc"])[:limit]
        err = _feed.get("mkt_disc_err")
        out = {"items": items, "ts": ctx.clock.now().isoformat(),
               "data_source": ctx.config.data_source, "scope": "market",
               "count": len(_feed["mkt_disc"])}
        # 갱신 지연: 최근 성공이 오래됐고(>10분) 데이터는 캐시로 떠 있는 상태
        ok_at = _feed.get("mkt_disc_ok", 0.0)
        if items and ok_at:
            stale = _time.time() - ok_at
            if stale > 600:        # 10분 이상 새 데이터 못 받음
                out["degraded"] = True
                out["stale_sec"] = int(stale)
        if items and not ok_at:
            # 메모리 성공 기록은 없지만 디스크 캐시로 표시 중 → 지연 표시
            _, ts = _load_feed_cache("disc")
            if ts:
                out["degraded"] = True
                out["cached_at"] = ts
        if not items:
            if not ctx.config.dart_api_key:
                out["error"] = "DART 키가 설정되지 않았습니다 (.env 의 DART_API_KEY)."
            elif err:
                out["error"] = err
            else:
                out["error"] = "최근 공시가 없습니다(주말·휴일이면 다음 거래일까지 비어 있을 수 있습니다)."
        return out

    # 정부정책 캐시(메모리) — 정책 RSS는 자주 안 바뀌므로 10분 캐시
    _policy_cache = {"items": [], "at": 0.0, "ok": 0.0}

    @app.get("/api/feed/hot_disclosures")
    def feed_hot_disclosures(limit: int = 20) -> dict:
        """오늘의 주요 공시 — 전체 공시 중 이벤트성(유상증자·M&A·자사주·배당·실적 등)만
        골라 유형 라벨과 함께 제공. DART 공식 데이터, 제목+링크만(본문 미복제). 추천 아님."""
        from app.providers.dart import classify_disclosure
        # 기존 시장 공시 캐시 재사용(_feed['mkt_disc'])
        raw = []
        try:
            raw = _dedupe(_feed.get("mkt_disc") or [])
        except Exception:
            raw = (_feed.get("mkt_disc") or [])
        hot = []
        for it in raw:
            cls = classify_disclosure(it.get("title", ""))
            if not cls:
                continue
            hot.append({
                "title": it.get("title", ""),
                "corp": it.get("corp", ""),
                "symbol": it.get("symbol", ""),
                "market": it.get("market", ""),
                "url": it.get("url", ""),
                "published_at": it.get("published_at", ""),
                "date_only": it.get("date_only", True),   # DART 공시는 날짜만
                "event_type": cls["type"],
                "icon": cls["icon"],
                "importance": cls["importance"],
            })
        # 중요도 높은 순 → 최신 순
        hot.sort(key=lambda x: (x["importance"], x.get("published_at", "")), reverse=True)
        err = _feed.get("mkt_disc_err")
        out = {"items": hot[:limit], "ts": ctx.clock.now().isoformat(),
               "count": len(hot),
               "attribution": "금융감독원 전자공시(DART) · 제목·링크만 · 투자권유 아님"}
        if not hot and err:
            out["error"] = "공시를 불러오지 못했습니다: " + str(err)
        elif not hot:
            out["note"] = "현재 표시할 주요 공시가 없습니다(장 시간외/휴장일 가능)."
        return out

    @app.get("/api/feed/policy")
    def feed_policy(limit: int = 30) -> dict:
        """정부정책 피드 — 정책브리핑·부처 보도자료(공공누리). 제목+링크+출처만."""
        prov = getattr(ctx, "policy_news", None)
        if prov is None or ctx.config.data_source == "mock":
            # mock/비활성: 데모 안내(빈 목록)
            return {"items": [], "ts": ctx.clock.now().isoformat(),
                    "data_source": ctx.config.data_source,
                    "note": "정부정책 피드는 라이브에서 표시됩니다(정책브리핑·부처 RSS)."}
        now_t = _time.time()
        # 15분 캐시(부처 RSS는 자주 안 바뀜)
        if not _policy_cache["items"] or now_t - _policy_cache["at"] > 900:
            _policy_cache["at"] = now_t
            try:
                fetched = prov.fetch_all(ctx.clock.now())
                if fetched:
                    _policy_cache["items"] = fetched
                    _policy_cache["ok"] = now_t
                    _persist_feed("policy", fetched)
                elif _policy_cache["items"]:
                    # 이번엔 못 받았지만 이전 데이터가 있으면 그대로 유지(지연 표시 안 함)
                    _policy_cache["ok"] = _policy_cache.get("ok", now_t)
            except Exception:
                pass
        items = _policy_cache["items"][:limit]
        degraded = False; cached_at = None
        if not items:
            cached, ts = _load_feed_cache("policy")
            if cached:
                # 디스크 캐시라도 데이터가 있으면 정상 표시(지연 딱지 X). 시간만 참고로.
                items = cached[:limit]; cached_at = ts
                _policy_cache["items"] = cached
                if not _policy_cache.get("ok"):
                    _policy_cache["ok"] = now_t
        out = {"items": items, "ts": ctx.clock.now().isoformat(),
               "data_source": ctx.config.data_source,
               "attribution": "공공누리 — 정책브리핑(korea.kr) 및 각 부처 보도자료"}
        ok_at = _policy_cache.get("ok", 0.0)
        # 지연 표시는 '데이터가 아주 오래(6시간)됐을 때'만. 부처 RSS는 원래 갱신이 느림.
        if items and ok_at and (now_t - ok_at) > 21600:
            degraded = True; out["stale_sec"] = int(now_t - ok_at)
        if degraded:
            out["degraded"] = True
        if cached_at:
            out["cached_at"] = cached_at
        if not items:
            out["error"] = "정책 자료를 불러오지 못했습니다(운영자 패널 → 정부정책 RSS 점검으로 주소 확인)."
        return out

    @app.get("/api/feed/policy/refresh")
    def feed_policy_refresh(x_admin_token: str = "") -> dict:
        """운영자: 정책 캐시를 비우고 즉시 새로 수집(낡은 캐시 강제 교체).
        RSS 주소를 고친 뒤 6/28 같은 옛 데이터가 남아있을 때 사용."""
        if not _is_admin_token(x_admin_token):
            raise HTTPException(403, "운영자 권한이 필요합니다.")
        prov = getattr(ctx, "policy_news", None)
        if prov is None:
            return {"ok": False, "error": "정책 provider 비활성"}
        # 메모리·디스크 캐시 모두 초기화
        _policy_cache["items"] = []
        _policy_cache["at"] = 0.0
        _policy_cache["ok"] = 0.0
        try:
            ctx.store.set_setting("feedcache:policy", "[]", ctx.clock.now().isoformat())
        except Exception:
            pass
        # 즉시 재수집
        try:
            fetched = prov.fetch_all(ctx.clock.now())
        except Exception as e:
            return {"ok": False, "error": f"수집 오류: {e}"}
        now_t = _time.time()
        if fetched:
            _policy_cache["items"] = fetched
            _policy_cache["at"] = now_t
            _policy_cache["ok"] = now_t
            _persist_feed("policy", fetched)
            newest = fetched[0].get("published_at", "") if fetched else ""
            return {"ok": True, "count": len(fetched), "newest": newest,
                    "msg": f"{len(fetched)}건 새로 수집됨(최신: {newest[:10]})"}
        return {"ok": False, "count": 0,
                "error": "수집 결과 0건. 운영자 패널 → 정부정책 RSS 점검으로 어느 주소가 죽었는지 확인하세요.",
                "last_error": getattr(prov, "last_error", None)}

    @app.get("/api/feed/policy/diag")
    def feed_policy_diag(x_admin_token: str = "") -> dict:
        """정책 RSS 진단 — 각 부처 주소가 실제로 응답·파싱되는지 운영자가 확인.
        어떤 주소가 살아있는지/죽었는지 표로 보여줘 URL 보정에 사용."""
        if not _is_admin_token(x_admin_token):
            raise HTTPException(403, "운영자 권한이 필요합니다.")
        prov = getattr(ctx, "policy_news", None)
        if prov is None:
            return {"ok": False, "error": "정책 provider가 비활성입니다(RECO_POLICY_NEWS=1)."}
        results = []
        now = ctx.clock.now()
        for src in prov.sources:
            row = {"name": src["name"], "url": src["url"], "ok": False, "count": 0, "error": ""}
            try:
                text = prov._fetch_text(src["url"])
                if not text:
                    row["error"] = "응답 없음(주소 오류·차단·점검 가능)"
                else:
                    parsed = prov.parse_rss(text, src, now)
                    row["count"] = len(parsed)
                    row["ok"] = len(parsed) > 0
                    if not parsed:
                        row["error"] = "응답은 받았으나 항목 0 (RSS 형식 다름 가능)"
            except Exception as e:
                row["error"] = f"오류: {e}"
            results.append(row)
        alive = sum(1 for r in results if r["ok"])
        return {"ok": True, "alive": alive, "total": len(results), "sources": results,
                "hint": "ok=false 인 주소는 각 부처 누리집의 'RSS' 메뉴에서 정확한 주소로 "
                        "app/providers/policy_news.py 의 SOURCES 를 수정하세요."}

    # ─── 환율(한국수출입은행) ───────────────────────────────
    _fx_cache = {"data": None, "at": 0.0}

    @app.get("/api/fx")
    def fx_rates() -> dict:
        """환율 — 원/달러·엔·유로 등(한국수출입은행, 공공정보)."""
        prov = getattr(ctx, "fx", None)
        if prov is None:
            return {"rates": [], "note": "환율은 EXIM_API_KEY 설정 시 표시됩니다.",
                    "attribution": "출처: 한국수출입은행"}
        now_t = _time.time()
        # 30분 캐시(환율 고시는 자주 안 바뀜)
        if not _fx_cache["data"] or now_t - _fx_cache["at"] > 1800:
            try:
                _fx_cache["data"] = prov.fetch_latest(ctx.clock.now())
                _fx_cache["at"] = now_t
            except Exception:
                pass
        return _fx_cache["data"] or {"rates": [], "attribution": "출처: 한국수출입은행",
                                     "error": "환율을 불러오지 못했습니다."}

    # ─── 기업 이벤트(공공데이터포털: 공시일정·보호예수·자사주) ───
    _corp_event_cache = {}   # kind -> (data, at)

    def _corp_events(kind: str, params: dict, ttl: int = 1800) -> dict:
        """공공데이터포털에서 기업 이벤트를 가져와 캐시. 사실 정보만, 추천 없음."""
        prov = getattr(ctx, "public_data", None)
        if prov is None:
            return {"items": [], "note": "DATA_GO_KR_KEY 설정 시 표시됩니다(공공데이터포털).",
                    "attribution": "출처: 금융위원회·한국예탁결제원 (공공데이터포털)"}
        now_t = _time.time()
        cached = _corp_event_cache.get(kind)
        if cached and now_t - cached[1] < ttl:
            return cached[0]
        try:
            rows = prov.fetch(kind, params)
        except Exception:
            rows = []
        out = {"items": rows[:50],
               "attribution": "출처: 금융위원회·한국예탁결제원 (공공데이터포털 data.go.kr)",
               "note": "공시일 기준 자료(실시간 아님). 사실 정보이며 투자권유가 아닙니다."}
        if not rows:
            out["error"] = "자료를 불러오지 못했습니다(키/네트워크/주말 점검)."
        _corp_event_cache[kind] = (out, now_t)
        return out

    @app.get("/api/corp/dividend")
    def corp_dividend() -> dict:
        return _corp_events("dividend", {})

    @app.get("/api/corp/rights")
    def corp_rights() -> dict:
        return _corp_events("rights", {})

    @app.get("/api/corp/treasury")
    def corp_treasury() -> dict:
        return _corp_events("treasury", {})

    @app.get("/api/corp/lockup")
    def corp_lockup() -> dict:
        return _corp_events("lockup", {})

    @app.get("/api/corp/finance")
    def corp_finance(name: str = "") -> dict:
        """기업 재무정보(요약) — 종목명으로 조회. 매출·영업이익·순이익 등."""
        params = {"crno": "", "corpNm": name} if name else {}
        return _corp_events("finance", params, ttl=3600)

    @app.get("/api/corp/dividend_detail")
    def corp_dividend_detail(name: str = "") -> dict:
        """주식 배당 상세 — 배당기준일·지급일·배당률·주식종류."""
        params = {"corpNm": name} if name else {}
        return _corp_events("dividend_detail", params, ttl=3600)

    @app.get("/api/corp/rights_schedule")
    def corp_rights_schedule(name: str = "") -> dict:
        """주식 권리일정 — 배당·증자·교환·감자 등 권리행사 일정."""
        params = {"corpNm": name} if name else {}
        return _corp_events("rights_schedule", params, ttl=3600)

    @app.get("/api/corp/diag")
    def corp_diag(x_admin_token: str = "") -> dict:
        """운영자용 진단 — 각 엔드포인트를 실제 호출해 성공/실패를 점검.
        DATA_GO_KR_KEY 발급 후 주소가 맞는지 확인하는 용도. 키 값은 노출 안 함."""
        if not _is_admin_token(x_admin_token):
            return {"ok": False, "error": "운영자 토큰이 필요합니다(x-admin-token)."}
        prov = getattr(ctx, "public_data", None)
        if prov is None:
            return {"ok": False, "error": "DATA_GO_KR_KEY 가 설정되지 않았습니다.",
                    "hint": "Render Environment 에 DATA_GO_KR_KEY 를 넣고 재배포하세요."}
        results = []
        for kind in ["dividend", "rights", "treasury", "lockup", "corp",
                     "finance", "dividend_detail", "rights_schedule"]:
            try:
                results.append(prov.diagnose(kind))
            except Exception as e:
                results.append({"kind": kind, "ok": False, "reason": str(e)})
        alive = sum(1 for r in results if r.get("ok"))
        return {"ok": True, "alive": alive, "total": len(results), "results": results,
                "hint": "ok=false 면 해당 종류의 정확한 URL을 공공데이터포털 Swagger 명세에서 "
                        "확인해 환경변수(RECO_CORP_EP_DIVIDEND 등)로 덮어쓰거나 "
                        "app/providers/public_data.py 의 ENDPOINTS 를 수정하세요."}

    @app.get("/api/feed/news")
    def feed_news(limit: int = 40) -> dict:
        _market_news_refresh(6)
        items = _feed_aggregate("news", limit)
        # 라이브인데 결과가 비면 디스크 캐시로 폴백(화면 안 비게)
        degraded = False; cached_at = None
        if not items and ctx.config.data_source != "mock":
            cached, ts = _load_feed_cache("news")
            if cached:
                items = cached[:limit]; degraded = True; cached_at = ts
        out = {"items": items, "ts": ctx.clock.now().isoformat(),
               "data_source": ctx.config.data_source, "press_enabled": _press_on()}
        # 최근 성공이 오래됐으면 갱신지연 표시
        ok_at = _feed.get("news_ok", 0.0)
        if items and ok_at and (_time.time() - ok_at) > 600:
            degraded = True; out["stale_sec"] = int(_time.time() - ok_at)
        if degraded:
            out["degraded"] = True
            if cached_at:
                out["cached_at"] = cached_at
        return out

    @app.get("/api/feed/reports")
    def feed_reports(limit: int = 40) -> dict:
        """시장 전체의 증권사 리포트 동향(목표주가 기사) 모음, 최신순 + 중복 제거."""
        _reports_refresh_some(4)
        items = []
        if ctx.config.data_source == "mock":
            for sym in (ctx.universe or [])[:40]:
                name = ctx.name_of(sym)
                nd = ctx.ssot.get(sym, Kind.NEWS.value)
                for it in (nd.payload.get("items") if nd else []) or []:
                    if it.get("source") == "뉴스":
                        items.append({**it, "symbol": sym, "name": name, "topic": "목표주가"})
        else:
            for q, arts in _feed["mkt_rep"].items():
                for a in arts:
                    items.append(a)
        items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        return {"items": _dedupe(items)[:limit], "ts": ctx.clock.now().isoformat(),
                "data_source": ctx.config.data_source, "press_enabled": _press_on()}

    _INDICES = [("0001", "코스피"), ("1001", "코스닥"), ("2001", "코스피200")]
    _idx_cache = {"ts": 0.0, "data": None}

    @app.get("/api/indices")
    def indices() -> dict:
        if _md_blocked():
            return {**_MARKETDATA_DISABLED, "items": []}
        """주요 지수. 국내(KIS 업종지수) + 해외(야후/stooq: 나스닥·S&P500·VIX·유가·미국채). 30초 캐시."""
        import random as _r
        now_t = _time.time()
        if _idx_cache["data"] is not None and now_t - _idx_cache["ts"] < 30:
            return _idx_cache["data"]

        domestic = []
        glob = []
        if ctx.config.data_source != "mock":
            # 국내 — KIS 업종지수
            if ctx.quote is not None and hasattr(ctx.quote, "index_price"):
                for code, name in _INDICES:
                    try:
                        d = ctx.quote.index_price(code)
                        if d.get("value"):
                            domestic.append({"code": code, "name": name, "dec": 2, **d})
                    except Exception:
                        pass
            # 국내 폴백 — KIS 지수가 안 되면 야후로 코스피/코스닥
            if not domestic and ctx.market is not None:
                try:
                    domestic = ctx.market.fetch_domestic()
                except Exception:
                    domestic = []
            # 해외 — 야후/stooq
            if ctx.market is not None:
                try:
                    glob = ctx.market.fetch_indices()
                except Exception:
                    glob = []
        else:
            base = {"0001": 2680.0, "1001": 860.0, "2001": 360.0}
            gbase = [("나스닥", 17800.0, 2), ("S&P500", 5430.0, 2), ("VIX", 14.2, 2),
                     ("WTI유가", 78.5, 2), ("미국채10년", 4.25, 3), ("원/달러", 1365.0, 2)]
            seed = ctx.clock.now().strftime("%Y%m%d%H%M")
            for code, name in _INDICES:
                rng = _r.Random(seed + code)
                pct = round(rng.uniform(-1.2, 1.2), 2)
                domestic.append({"code": code, "name": name, "dec": 2,
                                 "value": round(base[code] * (1 + pct / 100), 2),
                                 "change": round(base[code] * pct / 100, 2), "change_pct": pct})
            for name, b, dec in gbase:
                rng = _r.Random(seed + name)
                pct = round(rng.uniform(-1.5, 1.5), 2)
                glob.append({"name": name, "dec": dec, "value": round(b * (1 + pct / 100), dec),
                             "change": round(b * pct / 100, dec), "change_pct": pct, "source": "mock"})

        result = {"domestic": domestic, "global": glob,
                  "ts": ctx.clock.now().isoformat(), "data_source": ctx.config.data_source}
        _idx_cache["data"] = result
        _idx_cache["ts"] = now_t
        return result

    import time as _pt
    _presence: dict = {}   # cid -> last_seen ts (브라우저당 1명, F5 무관)

    @app.get("/api/chat")
    def chat_get(after_id: int = 0, limit: int = 200, cid: str = "", symbol: str = "") -> dict:
        """after_id 이후의 채팅 메시지(폴링 증분) + 접속자 수(cid 기반).
        symbol 지정 시 해당 종목 채팅방, 없으면 전체방(symbol 없는 메시지)."""
        room = symbol.strip() if symbol.strip() else None
        msgs = ctx.store.get_chat(after_id=after_id, limit=limit, symbol=room)
        last = msgs[-1]["id"] if msgs else after_id
        # ----- 접속자 집계 -----
        now = _pt.time()
        if cid:
            _presence[cid] = now
        for k in list(_presence.keys()):
            if _presence[k] < now - 300:
                _presence.pop(k, None)
        site = sum(1 for t in _presence.values() if t >= now - 70)   # 70초 이내 활동 = 접속
        chat_users = 0
        try:
            from datetime import timedelta as _td
            tcut = (ctx.clock.now() - _td(minutes=10)).isoformat()
            recent = ctx.store.get_chat(after_id=0, limit=500)
            chat_users = len({m.get("user", "") for m in recent
                              if m.get("created_at") and m["created_at"] >= tcut})
        except Exception:
            chat_users = 0
        return {"messages": msgs, "last_id": last,
                "presence": {"site": site, "chat": chat_users}}

    # 공시 유형 분류 — 캘린더에 표시할 '이벤트성' 공시만(제목 키워드 기반, 추측 없음)
    _DART_EVENT_RULES = [
        ("주주총회", "주총", "agm"),
        ("배당", "배당", "dividend"),
        ("유상증자", "유상증자", "rights"),
        ("무상증자", "무상증자", "rights"),
        ("합병", "합병", "ma"),
        ("분할", "분할", "ma"),
        ("자기주식", "자사주", "buyback"),
        ("실적", "실적", "earnings"),
        ("영업(잠정)실적", "잠정실적", "earnings"),
        ("증권신고서", "공모", "ipo"),
        ("공모", "공모", "ipo"),
    ]

    def _classify_disclosure(title: str):
        t = title or ""
        for kw, short, etype in _DART_EVENT_RULES:
            if kw in t:
                return short, etype
        return None, None

    def _dart_calendar_events(year: int, month: int) -> list:
        """해당 월의 DART 공시 중 '이벤트성' 공시를 캘린더 이벤트로 분류.
        날짜는 공시 접수일(실제 발생일) — 미래 예정일을 본문에서 추측하지 않는다.
        하루 1회 캐시(settings: dartcal:YYYY-MM)."""
        if ctx.config.data_source == "mock" or ctx.dart is None or not ctx.config.dart_api_key:
            return []
        now = ctx.clock.now()
        # 현재월 기준 과거~현재만 의미(미래월은 공시가 아직 없음)
        if (year, month) > (now.year, now.month):
            return []
        ck = f"dartcal:{year:04d}-{month:02d}"
        try:
            row = ctx.store.get_setting(ck)
            if row and row.get("value"):
                cached = _json_mod.loads(row["value"])
                # 당월은 6시간, 과거월은 7일 캐시
                age = (now - _t_rec_dt(row.get("updated_at"))).total_seconds() if row.get("updated_at") else 1e9
                ttl = 21600 if (year, month) == (now.year, now.month) else 604800
                if age < ttl:
                    return cached
        except Exception:
            pass
        # 이번 달 며칠치 공시를 가져온다(당월이면 오늘까지, 과거월이면 최대 31일)
        from datetime import date as _date
        try:
            if (year, month) == (now.year, now.month):
                days = now.day
            else:
                import calendar as _cal
                days = _cal.monthrange(year, month)[1]
            days = min(days, 31)
        except Exception:
            days = 7
        evs = []
        seen = set()
        try:
            # recent_disclosures 는 now 기준 과거 days. 과거월 조회는 부정확할 수 있어 당월 위주.
            items = ctx.dart.recent_disclosures(now, days=days, max_pages=5)
            pref = f"{year:04d}-{month:02d}"
            for it in items:
                pub = (it.get("published_at") or "")[:10]
                if not pub.startswith(pref):
                    continue
                short, etype = _classify_disclosure(it.get("title", ""))
                if not short:
                    continue
                nm = it.get("corp") or it.get("symbol") or ""
                key = (pub, nm, short)
                if key in seen:
                    continue
                seen.add(key)
                evs.append({"date": pub, "type": "disclosure",
                            "label": f"{nm} {short}", "etype": etype,
                            "url": it.get("url", "")})
            evs = evs[:120]
            ctx.store.set_setting(ck, _json_mod.dumps(evs, ensure_ascii=False), now.isoformat())
        except Exception:
            return []
        return evs

    @app.get("/api/calendar/coverage")
    def calendar_coverage(x_admin_token: str = "") -> dict:
        """운영자용 — 내장 연간 일정(FOMC·중앙은행 등)이 어느 해까지 채워졌는지.
        연말에 다음 해 일정 갱신이 필요한지 알려준다."""
        if not _is_admin_token(x_admin_token):
            return {"ok": False, "error": "운영자 토큰이 필요합니다(x-admin-token)."}
        try:
            from app.core.econ_events import schedule_coverage
            return {"ok": True, **schedule_coverage()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.get("/api/calendar")
    def market_calendar(year: int = 0, month: int = 0) -> dict:
        """증시 캘린더 — 휴장일·만기일·배당락(참고) + 운영자 등록 일정.
        시세·추천 없음. 공개 일정 정보만."""
        from app.core.calendar_events import computed_events
        now = ctx.clock.now()
        y = year or now.year
        m = month or now.month
        events = computed_events(y, m)
        # 운영자 등록 일정(settings: market_events = [{date,label,type}])
        try:
            row = ctx.store.get_setting("market_events")
            if row and row.get("value"):
                custom = _json_mod.loads(row["value"])
                pref = f"{y:04d}-{m:02d}"
                for e in custom:
                    ds = str(e.get("date", ""))
                    if ds.startswith(pref):
                        events.append({"date": ds, "type": e.get("type", "custom"),
                                       "label": str(e.get("label", ""))[:80]})
        except Exception:
            pass
        # DART 공시 이벤트(공개 데이터) — 실적·배당·주총·증자·공모 등
        try:
            events.extend(_dart_calendar_events(y, m))
        except Exception:
            pass
        events.sort(key=lambda e: e["date"])
        # 이번 주 요약(오늘~+7일) — 일정 미리보기 배너용
        week = []
        try:
            from datetime import timedelta as _td2
            from app.core.clock import to_kst as _tk2
            tod = _tk2(now).date()
            end7 = tod + _td2(days=7)
            for e in events:
                try:
                    ed = _dt_mod.date.fromisoformat(e["date"])
                except Exception:
                    continue
                if tod <= ed <= end7:
                    week.append(e)
        except Exception:
            pass
        return {"year": y, "month": m, "events": events,
                "week": week[:8], "today": now.strftime("%Y-%m-%d")}

    @app.get("/api/calendar.ics")
    def calendar_ics(year: int = 0, month: int = 0):
        """캘린더 일정을 iCal(.ics)로 내보내기 — 구글/애플 캘린더에 추가용. 공개 일정만."""
        from app.core.calendar_events import computed_events
        now = ctx.clock.now()
        y = year or now.year
        m = month or now.month
        events = computed_events(y, m)
        try:
            events.extend(_dart_calendar_events(y, m))
        except Exception:
            pass
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
                 "PRODID:-//모두의 리서치 모니터//증시 캘린더//KR", "CALSCALE:GREGORIAN"]
        for e in events:
            ds = e["date"].replace("-", "")
            uid = f"{ds}-{abs(hash(e['label'])) % 100000}@reco-monitor"
            lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTART;VALUE=DATE:{ds}",
                      f"SUMMARY:{e['label']}", "END:VEVENT"]
        lines.append("END:VCALENDAR")
        ics = "\r\n".join(lines)
        try:
            from fastapi.responses import Response as _Resp
            return _Resp(content=ics, media_type="text/calendar",
                         headers={"Content-Disposition": f"attachment; filename=market_{y}_{m:02d}.ics"})
        except Exception:
            return {"ics": ics}

    @app.get("/sitemap.xml")
    def sitemap():
        """SEO 사이트맵 — 메인 + 모든 용어/공시 페이지. 검색엔진 색인용."""
        from app.content.glossary import GLOSSARY, DISCLOSURE_GUIDE, slugify
        from urllib.parse import quote
        base = (getattr(ctx.config, "site_url", "") or "").rstrip("/")
        urls = ['<url><loc>%s/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>' % base]
        urls.append('<url><loc>%s/glossary</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>' % base)
        urls.append('<url><loc>%s/guide</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>' % base)
        urls.append('<url><loc>%s/start</loc><changefreq>monthly</changefreq><priority>0.7</priority></url>' % base)
        urls.append('<url><loc>%s/faq</loc><changefreq>monthly</changefreq><priority>0.7</priority></url>' % base)
        for g in GLOSSARY:
            urls.append('<url><loc>%s/term/%s</loc><changefreq>monthly</changefreq><priority>0.6</priority></url>'
                        % (base, quote(slugify(g["term"]))))
        for d in DISCLOSURE_GUIDE:
            if d.get("etype"):
                urls.append('<url><loc>%s/guide/%s</loc><changefreq>monthly</changefreq><priority>0.6</priority></url>'
                            % (base, quote(d["etype"])))
        xml = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               + "".join(urls) + '</urlset>')
        try:
            from fastapi.responses import Response as _Resp
            return _Resp(content=xml, media_type="application/xml")
        except Exception:
            return {"xml": xml}

    def _seo_page(title: str, desc: str, body_html: str, canonical_path: str,
                  jsonld: str = "") -> Any:
        """검색엔진 색인용 독립 HTML 페이지(SPA와 별개, 깔끔한 콘텐츠)."""
        import html as _h
        base = (getattr(ctx.config, "site_url", "") or "").rstrip("/")
        canon = base + canonical_path if base else canonical_path
        page = (
            '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            '<title>' + _h.escape(title) + '</title>'
            '<meta name="description" content="' + _h.escape(desc) + '">'
            '<meta property="og:title" content="' + _h.escape(title) + '">'
            '<meta property="og:description" content="' + _h.escape(desc) + '">'
            '<meta property="og:type" content="article"><meta property="og:locale" content="ko_KR">'
            '<link rel="canonical" href="' + _h.escape(canon) + '">'
            + (jsonld or "") +
            '<style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
            'max-width:720px;margin:0 auto;padding:24px 18px;line-height:1.7;color:#1a1a1a;background:#fff}'
            'a{color:#1a7f72;text-decoration:none}a:hover{text-decoration:underline}'
            'h1{font-size:24px;margin:0 0 4px}.full{color:#666;font-size:14px;margin-bottom:16px}'
            '.cat{display:inline-block;font-size:12px;background:#eef5f4;color:#1a7f72;padding:2px 9px;border-radius:6px;margin-bottom:14px}'
            '.desc{font-size:16px;color:#222}.nav{margin:24px 0;padding-top:16px;border-top:1px solid #eee;font-size:14px}'
            '.row{margin:8px 0}.k{font-weight:700;color:#555;display:inline-block;min-width:60px}'
            '.warn{color:#c0612f}.foot{margin-top:28px;font-size:12px;color:#999}'
            '.applink{display:inline-block;margin-top:10px;background:#1a7f72;color:#fff;padding:9px 16px;border-radius:8px}'
            '</style></head><body>' + body_html +
            '<div class="foot">본 내용은 투자 참고용 정보이며 투자자문·매매추천이 아닙니다. 투자 판단과 책임은 본인에게 있습니다.</div>'
            '</body></html>'
        )
        try:
            from fastapi.responses import HTMLResponse as _H
            return _H(content=page)
        except Exception:
            return page

    @app.get("/term/{slug}")
    def seo_term(slug: str) -> Any:
        from app.content.glossary import term_by_slug
        import html as _h
        t = term_by_slug(slug)
        if not t:
            return _seo_page("용어를 찾을 수 없습니다 — 모두의 리서치 모니터",
                             "요청하신 증시 용어를 찾을 수 없습니다.",
                             '<h1>용어를 찾을 수 없습니다</h1><div class="nav"><a href="/glossary">전체 용어 사전 보기</a></div>',
                             "/term/" + slug)
        title = f"{t['term']} 뜻 — {t['full']} | 증시 용어 사전"
        desc = (t["desc"][:150])
        jsonld = ('<script type="application/ld+json">'
                  '{"@context":"https://schema.org","@type":"DefinedTerm",'
                  '"name":"' + _h.escape(t["term"]) + '",'
                  '"description":"' + _h.escape(t["desc"]) + '"}'
                  '</script>')
        body = (
            '<div class="cat">' + _h.escape(t["cat"]) + '</div>'
            '<h1>' + _h.escape(t["term"]) + '</h1>'
            '<div class="full">' + _h.escape(t["full"]) + '</div>'
            '<div class="desc">' + _h.escape(t["desc"]) + '</div>'
            '<div class="nav"><a href="/glossary">📖 전체 증시 용어 사전</a> · '
            '<a href="/">🏠 모두의 리서치 모니터</a></div>'
            '<a class="applink" href="/">증시 캘린더·공시 보러 가기 →</a>'
        )
        return _seo_page(title, desc, body, "/term/" + slug, jsonld)

    @app.get("/guide/{etype}")
    def seo_guide(etype: str) -> Any:
        from app.content.glossary import guide_by_etype
        import html as _h
        d = guide_by_etype(etype)
        if not d:
            return _seo_page("가이드를 찾을 수 없습니다 — 모두의 리서치 모니터",
                             "요청하신 공시 가이드를 찾을 수 없습니다.",
                             '<h1>가이드를 찾을 수 없습니다</h1><div class="nav"><a href="/guide">전체 공시 가이드 보기</a></div>',
                             "/guide/" + etype)
        title = f"{d['type']} 공시 읽는 법 | 전자공시(DART) 가이드"
        desc = (d["what"][:150])
        body = (
            '<div class="cat">공시 읽는 법</div>'
            '<h1>' + _h.escape(d["type"]) + '</h1>'
            '<div class="row"><span class="k">무엇</span>' + _h.escape(d["what"]) + '</div>'
            '<div class="row"><span class="k">읽는 법</span>' + _h.escape(d["read"]) + '</div>'
            '<div class="row"><span class="k warn">주의</span><span class="warn">' + _h.escape(d["caution"]) + '</span></div>'
            '<div class="nav"><a href="/guide">📑 전체 공시 읽는 법</a> · '
            '<a href="/">🏠 모두의 리서치 모니터</a></div>'
            '<a class="applink" href="/">전자공시 실시간 보러 가기 →</a>'
        )
        return _seo_page(title, desc, body, "/guide/" + etype)

    @app.get("/glossary")
    def seo_glossary_index() -> Any:
        from app.content.glossary import GLOSSARY, categories, slugify
        import html as _h
        from urllib.parse import quote
        rows = ""
        for cat in categories():
            rows += '<h2 style="font-size:17px;margin:20px 0 6px;color:#1a7f72">' + _h.escape(cat) + '</h2>'
            for g in GLOSSARY:
                if g["cat"] == cat:
                    rows += ('<div class="row"><a href="/term/' + quote(slugify(g["term"])) + '"><b>'
                             + _h.escape(g["term"]) + '</b></a> — ' + _h.escape(g["desc"][:60]) + '…</div>')
        body = ('<h1>증시 용어 사전</h1>'
                '<div class="desc">초보 투자자를 위한 ' + str(len(GLOSSARY)) + '개 증시 용어를 쉽게 설명합니다.</div>'
                + rows +
                '<div class="nav"><a href="/">🏠 모두의 리서치 모니터</a></div>')
        return _seo_page("증시 용어 사전 — PER·PBR·공매도·배당락 쉬운 설명",
                         "초보 투자자를 위한 증시 용어 사전. PER, PBR, 공매도, 배당락, 유상증자, 네 마녀의 날 등 핵심 용어를 쉽게 설명합니다.",
                         body, "/glossary")

    @app.get("/guide")
    def seo_guide_index() -> Any:
        from app.content.glossary import DISCLOSURE_GUIDE
        import html as _h
        from urllib.parse import quote
        rows = ""
        for d in DISCLOSURE_GUIDE:
            link = ('/guide/' + quote(d["etype"])) if d.get("etype") else "#"
            rows += ('<div class="row"><a href="' + link + '"><b>' + _h.escape(d["type"]) + '</b></a> — '
                     + _h.escape(d["what"][:60]) + '…</div>')
        body = ('<h1>공시 읽는 법 가이드</h1>'
                '<div class="desc">전자공시(DART) 유형별로 무엇을 보고 어떻게 해석하는지 쉽게 안내합니다.</div>'
                + rows +
                '<div class="nav"><a href="/">🏠 모두의 리서치 모니터</a></div>')
        return _seo_page("공시 읽는 법 가이드 — 전자공시(DART) 유형별 해설",
                         "주주총회·배당·유상증자·실적·합병 등 전자공시(DART) 유형별로 무엇을 보고 어떻게 읽는지 쉽게 설명합니다.",
                         body, "/guide")

    @app.get("/start")
    def seo_start() -> Any:
        from app.content.glossary import START_GUIDE
        import html as _h
        rows = ""
        for s in START_GUIDE:
            rows += ('<div class="row"><b>' + str(s["step"]) + '. ' + _h.escape(s["title"]) + '</b>'
                     '<div style="margin-top:4px;color:#333">' + _h.escape(s["body"]) + '</div></div>')
        body = ('<h1>주식 시작하기 — 초보 가이드</h1>'
                '<div class="desc">계좌 개설부터 거래 시간, 주문 방법, 위험 관리까지 단계별로 안내합니다.</div>'
                + rows +
                '<div class="nav"><a href="/faq">❓ 자주 묻는 질문</a> · <a href="/glossary">📖 용어사전</a> · '
                '<a href="/">🏠 모두의 리서치 모니터</a></div>')
        return _seo_page("주식 시작하기 — 초보 투자자 단계별 가이드",
                         "증권 계좌 개설, 거래 시간, 주문 방법, 기업 정보 확인, 위험 관리, 세금까지 — 주식 초보를 위한 단계별 가이드.",
                         body, "/start")

    @app.get("/faq")
    def seo_faq() -> Any:
        from app.content.glossary import FAQ
        import html as _h
        rows = ""
        jsonld_items = []
        for f in FAQ:
            rows += ('<div class="row"><b>Q. ' + _h.escape(f["q"]) + '</b>'
                     '<div style="margin-top:4px;color:#333">A. ' + _h.escape(f["a"]) + '</div></div>')
            jsonld_items.append('{"@type":"Question","name":"' + _h.escape(f["q"]).replace('"', "'") +
                                '","acceptedAnswer":{"@type":"Answer","text":"' +
                                _h.escape(f["a"]).replace('"', "'") + '"}}')
        jsonld = ('<script type="application/ld+json">'
                  '{"@context":"https://schema.org","@type":"FAQPage","mainEntity":['
                  + ",".join(jsonld_items) + ']}</script>')
        body = ('<h1>자주 묻는 질문 (FAQ)</h1>'
                '<div class="desc">증시 거래 시간, 휴장일, 전자공시, 배당 등 자주 묻는 질문을 모았습니다.</div>'
                + rows +
                '<div class="nav"><a href="/start">🚀 주식 시작하기</a> · <a href="/glossary">📖 용어사전</a> · '
                '<a href="/">🏠 모두의 리서치 모니터</a></div>')
        return _seo_page("자주 묻는 질문 (FAQ) — 증시 거래·휴장일·공시·배당",
                         "주식 거래 시간, 증시 휴장일, 전자공시(DART), 배당 권리일 등 초보 투자자가 자주 묻는 질문과 답변.",
                         body, "/faq", jsonld)

    @app.get("/robots.txt")
    def robots():
        base = (getattr(ctx.config, "site_url", "") or "").rstrip("/")
        txt = "User-agent: *\nAllow: /\n"
        if base:
            txt += f"Sitemap: {base}/sitemap.xml\n"
        try:
            from fastapi.responses import Response as _Resp
            return _Resp(content=txt, media_type="text/plain")
        except Exception:
            return {"txt": txt}

    @app.get("/api/admin/calendar")
    def admin_calendar_get(x_admin_token: str = "") -> dict:
        """운영자: 등록된 캘린더 일정 조회."""
        if not _is_admin_token(x_admin_token):
            raise HTTPException(403, "운영자 권한이 필요합니다.")
        try:
            row = ctx.store.get_setting("market_events")
            evs = _json_mod.loads(row["value"]) if row and row.get("value") else []
        except Exception:
            evs = []
        return {"events": evs}

    @app.post("/api/admin/calendar")
    def admin_calendar_set(request: Request, body: dict = None) -> dict:
        """운영자: 증시 캘린더 일정 등록/수정. body={events:[{date,label,type}]}"""
        if not _is_admin(request):
            raise HTTPException(403, "운영자 권한이 필요합니다.")
        evs = []
        for e in (body or {}).get("events", [])[:500]:
            ds = str(e.get("date", "")).strip()
            if len(ds) == 10 and ds[4] == "-":
                evs.append({"date": ds, "label": str(e.get("label", "")).strip()[:80],
                            "type": str(e.get("type", "custom")).strip()[:20] or "custom"})
        ctx.store.set_setting("market_events", _json_mod.dumps(evs, ensure_ascii=False),
                              ctx.clock.now().isoformat())
        return {"ok": True, "count": len(evs)}

    @app.get("/api/daily_tip")
    def daily_tip() -> dict:
        """오늘의 증시 상식 — 용어 사전에서 날짜 기준으로 하나 순환 표시(외부 데이터 없음)."""
        from app.content.glossary import GLOSSARY
        now = ctx.clock.now()
        doy = now.timetuple().tm_yday
        if not GLOSSARY:
            return {"tip": None}
        t = GLOSSARY[doy % len(GLOSSARY)]
        return {"date": now.strftime("%Y-%m-%d"), "tip": t}

    @app.get("/api/glossary")
    def glossary() -> dict:
        """증시 용어 사전 — 직접 작성한 정적 교육 콘텐츠(검색·카테고리용)."""
        from app.content.glossary import GLOSSARY, categories
        return {"terms": GLOSSARY, "categories": categories()}

    @app.get("/api/disclosure_guide")
    def disclosure_guide() -> dict:
        """공시 읽는 법 가이드 — 유형별 해설(자체 작성)."""
        from app.content.glossary import DISCLOSURE_GUIDE
        return {"guide": DISCLOSURE_GUIDE}

    @app.get("/api/start_guide")
    def start_guide() -> dict:
        """초보 가이드(단계별) + FAQ — 자체 작성 교육 콘텐츠."""
        from app.content.glossary import START_GUIDE, FAQ
        return {"steps": START_GUIDE, "faq": FAQ}

    @app.get("/api/legal/{doc}")
    def legal_doc(doc: str) -> dict:
        """법적 문서(이용약관/개인정보처리방침/면책조항) 텍스트 제공."""
        import os as _os
        names = {"terms": "이용약관.txt", "privacy": "개인정보처리방침.txt",
                 "disclaimer": "면책조항.txt"}
        fn = names.get(doc)
        if not fn:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        base = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        path = _os.path.join(base, "legal", fn)
        try:
            with open(path, encoding="utf-8") as f:
                return {"doc": doc, "title": fn.replace(".txt", ""), "text": f.read()}
        except Exception:
            return {"doc": doc, "title": fn.replace(".txt", ""),
                    "text": "문서를 불러올 수 없습니다. legal/ 폴더를 확인하세요."}

    @app.post("/api/chat")
    def chat_post(body: dict = None, request: Request = None) -> dict:
        from fastapi import HTTPException
        body = body or {}
        user = (body.get("user") or "익명").strip()[:24] or "익명"
        text = (body.get("text") or "").strip()[:1000]
        symbol = (body.get("symbol") or None)
        cid = (body.get("cid") or None)
        if not text:
            raise HTTPException(status_code=400, detail="빈 메시지")
        key = ("cid:" + cid) if cid else _client_key(request)
        # 속도 제한: 10초에 5건, 도배(동일 메시지 30초 내 반복) 차단
        if not _rate_check(key, "chat", 5, 10.0):
            raise HTTPException(status_code=429, detail="메시지를 너무 빠르게 보내고 있습니다. 잠시 후 다시 시도해 주세요.")
        if not _dup_check("chat:" + key, text):
            raise HTTPException(status_code=429, detail="같은 메시지를 연속으로 보낼 수 없습니다.")
        bad = _contains_banned(text)
        if bad:
            raise HTTPException(status_code=400,
                                detail="금지어가 포함되어 등록할 수 없습니다. 표현을 수정해 주세요.")
        mid = ctx.store.add_chat(user, text, ctx.clock.now().isoformat(), symbol, cid)
        return {"ok": True, "id": mid}

    @app.get("/api/notice")
    def notice_get() -> dict:
        """채팅 위 공지 — 개발자가 프로젝트 루트의 notice.txt 를 수정해서만 변경.
        UI 에서는 편집 불가(읽기 전용)."""
        text = ""
        try:
            import os as _os
            # 프로젝트 루트(= app/ 의 상위) 기준 notice.txt
            base = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
            path = _os.path.join(base, "notice.txt")
            if _os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    text = f.read().strip()[:500]
        except Exception:
            text = ""
        return {"text": text, "editable": False}

    @app.post("/api/notice")
    def notice_post(body: dict = None) -> dict:
        """공지 수정은 서버의 notice.txt 파일로만 가능(보안). API 편집 차단."""
        raise HTTPException(403, "공지는 서버의 notice.txt 파일에서만 수정할 수 있습니다.")

    @app.get("/api/ad")
    def ad() -> dict:
        """광고 영역. AdSense 게시자 ID가 설정되면 애드센스 모드, 아니면 수동 ad.txt 배너.
          · '광고문구'                         → 텍스트만 표시(링크 없음)
          · 'https://링크 | 광고문구'           → 클릭 가능한 텍스트 광고
          · 'https://링크 | https://이미지.jpg' → 이미지 배너
        파일이 없거나 비면 '광고 문의' 안내. URL 은 http(s):// 로 시작할 때만 링크 처리(안전)."""
        # AdSense 우선 — 게시자 ID가 있으면 클라이언트가 애드센스 유닛을 렌더
        pub = getattr(ctx.config, "adsense_pub", "") or ""
        if pub:
            return {"adsense": True, "pub": pub,
                    "slot": getattr(ctx.config, "adsense_slot", "") or "",
                    "label": "광고"}
        import os as _os, re as _re, html as _html
        web = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), "web")
        line = ""
        for fn in ("ad.txt", "ad.html"):
            try:
                with open(_os.path.join(web, fn), "r", encoding="utf-8") as f:
                    raw = f.read()
                # HTML 주석 전부 제거(중첩 예시로 인한 오작동 방지: 첫 <!-- 부터 마지막 --> 까지 통째로)
                raw = _re.sub(r"<!--.*-->", "", raw, flags=_re.S)
                # 첫 비어있지 않은 줄만 사용
                for ln in raw.splitlines():
                    if ln.strip():
                        line = ln.strip()
                        break
                if line:
                    break
            except Exception:
                continue
        if not line:
            return {"html": "", "empty": True}

        def _is_url(s: str) -> bool:
            return bool(_re.match(r"https?://[^\s|]+$", s.strip()))

        # '링크 | 콘텐츠' 또는 단독
        if "|" in line:
            left, _, right = line.partition("|")
            left, right = left.strip(), right.strip()
            link = left if _is_url(left) else ""
            content = right
        else:
            link = line if _is_url(line) else ""
            content = "" if link else line

        # 이미지 배너?
        if content and _is_url(content) and _re.search(r"\.(png|jpg|jpeg|gif|webp|svg)(\?|$)", content, _re.I):
            inner = f'<img src="{_html.escape(content, quote=True)}" alt="광고">'
        elif content:
            inner = _html.escape(content)
        elif link:
            inner = _html.escape(link)
        else:
            inner = ""
        if not inner:
            return {"html": "", "empty": True}
        if link:
            return {"html": f'<a href="{_html.escape(link, quote=True)}" target="_blank" rel="noopener">{inner}</a>',
                    "empty": False}
        return {"html": f'<span>{inner}</span>', "empty": False}

    @app.get("/api/board")
    def board_get(limit: int = Query(100, ge=1, le=200)) -> dict:
        """버그/건의 게시판 목록(최신순)."""
        try:
            return {"posts": ctx.store.get_posts(limit=limit)}
        except Exception as e:
            return {"posts": [], "error": str(e)}
        """버그/건의 게시판 목록(최신순)."""
        try:
            return {"posts": ctx.store.get_posts(limit=limit)}
        except Exception as e:
            return {"posts": [], "error": str(e)}

    @app.post("/api/board")
    def board_post(body: dict, request: Request = None) -> dict:
        """게시글 작성."""
        b = body or {}
        author = (str(b.get("author", "")).strip() or "익명")[:24]
        title = str(b.get("title", "")).strip()[:120]
        text = str(b.get("body", "")).strip()[:4000]
        category = (str(b.get("category", "")).strip() or "버그")[:12]
        if not title or not text:
            raise HTTPException(400, "제목과 내용을 입력하세요.")
        key = _client_key(request)
        if not _rate_check(key, "board", 3, 60.0):
            raise HTTPException(429, "게시글을 너무 자주 작성하고 있습니다. 잠시 후 다시 시도해 주세요.")
        if not _dup_check("board:" + key, title + text):
            raise HTTPException(429, "같은 내용을 연속으로 작성할 수 없습니다.")
        bad = _contains_banned(title + " " + text)
        if bad:
            raise HTTPException(400, "금지어가 포함되어 등록할 수 없습니다. 표현을 수정해 주세요.")
        pid = ctx.store.add_post(author, title, text, ctx.clock.now().isoformat(), category)
        return {"ok": True, "id": pid}

    # ===== 신고 / 운영자(모더레이션) =====
    @app.post("/api/report")
    def report_create(body: dict = None, request: Request = None) -> dict:
        """사용자 신고 — 채팅/게시글의 부적절한 내용을 운영자에게 신고.
        같은 대상에 서로 다른 신고자 기준 임계치(기본 3) 이상 누적되면 자동 숨김."""
        b = body or {}
        ttype = str(b.get("target_type", "")).strip()
        tid = b.get("target_id")
        reason = str(b.get("reason", "")).strip()[:200]
        cid = str(b.get("cid", "")).strip()[:64]
        if ttype not in ("chat", "post") or not isinstance(tid, int):
            raise HTTPException(400, "잘못된 신고 대상입니다.")
        key = ("cid:" + cid) if cid else _client_key(request)
        # 신고 남용 방지: 1분에 10건, 같은 대상 중복 신고 차단(30초)
        if not _rate_check(key, "report", 10, 60.0):
            raise HTTPException(429, "신고가 너무 많습니다. 잠시 후 다시 시도해 주세요.")
        if not _dup_check("report:" + key, f"{ttype}:{tid}"):
            raise HTTPException(429, "같은 대상을 연속으로 신고할 수 없습니다.")
        rid = ctx.store.add_report(ttype, tid, reason, cid, ctx.clock.now().isoformat())
        # 자동 숨김: 고유 신고자 수가 임계치 이상이면 즉시 숨김(운영자 부재 시에도 노출 차단)
        auto = False
        try:
            threshold = int(getattr(ctx.config, "report_auto_hide", 3) or 3)
            uniq = ctx.store.distinct_reporters(ttype, tid)
            if uniq >= threshold:
                if ttype == "chat":
                    ctx.store.hide_chat(tid, True)
                else:
                    ctx.store.hide_post(tid, True)
                auto = True
        except Exception:
            pass
        return {"ok": True, "id": rid, "auto_hidden": auto,
                "message": ("신고가 누적되어 자동으로 숨김 처리되었습니다." if auto
                            else "신고가 접수되었습니다. 운영자가 확인 후 조치합니다.")}

    @app.get("/api/admin/reports")
    def admin_reports(status: str = "open", x_admin_token: str = "") -> dict:
        """운영자: 신고 목록(미처리/처리). request 없이 쿼리 토큰만 사용."""
        if not _is_admin_token(x_admin_token):
            raise HTTPException(403, "운영자 권한이 필요합니다.")
        return {"reports": ctx.store.get_reports(status=status),
                "open_count": ctx.store.report_count("open")}

    @app.get("/api/admin/token_check")
    def admin_token_check(x_admin_token: str = "") -> dict:
        """토큰 진단 — 값은 노출하지 않고, 일치 여부·길이만 알려준다.
        request 객체 없이 쿼리 파라미터(x_admin_token)만 받는다(환경 호환성)."""
        cfg = (getattr(ctx.config, "admin_token", "") or "")
        hdr = x_admin_token or ""
        cfg_s, hdr_s = cfg.strip(), hdr.strip()
        return {
            "server_token_set": bool(cfg_s),
            "server_len": len(cfg_s),
            "your_len": len(hdr_s),
            "match": (hdr_s == cfg_s and bool(cfg_s)),
            "first_char_match": bool(cfg_s and hdr_s and cfg_s[0] == hdr_s[0]),
            "last_char_match": bool(cfg_s and hdr_s and cfg_s[-1] == hdr_s[-1]),
            "hint": ("일치합니다." if (hdr_s == cfg_s and cfg_s)
                     else "값이 다릅니다. 길이가 같은데 안 맞으면 대소문자/숫자(0,1) 혼동, "
                          "길이가 다르면 입력 누락·자동완성·복사 오류일 수 있습니다."),
        }

    @app.post("/api/admin/moderate")
    def admin_moderate(request: Request, body: dict = None) -> dict:
        """운영자: 숨김/해제 + 신고 처리. body={action, target_type, target_id, report_id?}"""
        if not _is_admin(request):
            raise HTTPException(403, "운영자 권한이 필요합니다.")
        b = body or {}
        action = b.get("action")           # 'hide' | 'unhide'
        ttype = b.get("target_type")       # 'chat' | 'post'
        tid = b.get("target_id")
        hide = (action == "hide")
        if ttype == "chat" and isinstance(tid, int):
            ctx.store.hide_chat(tid, hide)
        elif ttype == "post" and isinstance(tid, int):
            ctx.store.hide_post(tid, hide)
        else:
            raise HTTPException(400, "잘못된 대상입니다.")
        if isinstance(b.get("report_id"), int):
            ctx.store.resolve_report(b["report_id"])
        return {"ok": True}

    @app.get("/api/admin/banned_words")
    def admin_banned_get(x_admin_token: str = "") -> dict:
        """운영자: 금지어 목록 조회."""
        if not _is_admin_token(x_admin_token):
            raise HTTPException(403, "운영자 권한이 필요합니다.")
        return {"words": _banned_words()}

    @app.post("/api/admin/banned_words")
    def admin_banned_set(request: Request, body: dict = None) -> dict:
        """운영자: 금지어 목록 갱신. body={words:[...]}"""
        if not _is_admin(request):
            raise HTTPException(403, "운영자 권한이 필요합니다.")
        words = [str(w).strip()[:40] for w in (body or {}).get("words", []) if str(w).strip()][:300]
        ctx.store.set_setting("banned_words", _json_mod.dumps(words, ensure_ascii=False),
                              ctx.clock.now().isoformat())
        return {"ok": True, "words": words}

    @app.get("/api/admin/status")
    def admin_status(x_admin_token: str = "") -> dict:
        """운영자 인증 확인 + 미처리 신고 수."""
        ok = _is_admin_token(x_admin_token)
        return {"is_admin": ok,
                "open_reports": ctx.store.report_count("open") if ok else None,
                "admin_enabled": bool(getattr(ctx.config, "admin_token", ""))}

    @app.get("/api/admin/stats")
    def admin_stats(days: int = 7, x_admin_token: str = "") -> dict:
        """운영자: 일별 접속자·채팅·게시글 수 + 신고 처리 현황(집계 수치만)."""
        if not _is_admin_token(x_admin_token):
            raise HTTPException(403, "운영자 권한이 필요합니다.")
        try:
            return {"ok": True, "stats": ctx.store.admin_stats(days=max(1, min(days, 31)))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.get("/api/market_flow")
    def market_flow(market: str = "KOSPI", days: int = Query(30, ge=5, le=120)) -> dict:
        """코스피 오실레이터 + 수급 연결.
        오실레이터 = 코스피 지수 종가의 RSI(14) — 지수 데이터만 있으면 항상 작동.
        보조로 적재된 코스피 종목들의 외국인·기관 순매수를 날짜별 합산해 함께 표시."""
        mkt = (market or "KOSPI").upper()
        if mkt not in ("KOSPI", "KOSDAQ"):
            raise HTTPException(404, f"unknown market: {mkt}")
        sym_map = {"KOSPI": "^KS11", "KOSDAQ": "^KQ11"}

        # 1) 지수 종가 시계열 → RSI 오실레이터 (핵심)
        closes: list = []
        try:
            if ctx.market is not None and hasattr(ctx.market, "fetch_index_series"):
                s = ctx.market.fetch_index_series(sym_map[mkt], "6mo") or \
                    ctx.market.fetch_index_series(sym_map[mkt], "1y")
                if s:
                    closes = [float(x) for x in s if x is not None]
        except Exception:
            closes = []
        idx = closes[-days:] if closes else []
        osc = _rsi_series(idx, period=min(14, max(2, len(idx) - 1))) if len(idx) >= 3 else []

        # 2) 수급 연결(보조) — 적재된 종목 외국인/기관 순매수 합산
        per_date: dict[str, dict] = {}
        n_syms = 0
        for sym in ctx.ssot.symbols():
            if ctx.service._market(sym) != mkt:
                continue
            dp = ctx.ssot.get(sym, "supply")
            if dp is None:
                continue
            n_syms += 1
            for d in (dp.payload.get("daily", []) or [])[-days:]:
                ds = d.get("date")
                if not ds:
                    continue
                acc = per_date.setdefault(ds, {"foreign": 0.0, "inst": 0.0})
                acc["foreign"] += d.get("foreign_net") or 0.0
                acc["inst"] += d.get("inst_net") or 0.0
        sdates = sorted(per_date.keys())[-days:]
        foreign = [round(per_date[d]["foreign"], 1) for d in sdates]
        inst = [round(per_date[d]["inst"], 1) for d in sdates]
        cum, run = [], 0.0
        for v in foreign:
            run += v
            cum.append(round(run, 1))
        # 지수 시계열이 없으면(오프라인) 수급 누적 RSI 로 폴백
        osc_basis = "index"
        if not osc and cum:
            osc = _rsi_series(cum, period=min(14, max(2, len(cum) - 1)))
            osc_basis = "supply"

        return {"market": mkt, "symbols": n_syms,
                "index": [round(c, 2) for c in idx], "oscillator": osc,
                "osc_basis": osc_basis,
                "dates": sdates, "foreign": foreign, "inst": inst, "cum_foreign": cum}

    @app.get("/api/verdict/calibration")
    def calibration() -> dict:
        return {"calibration": ctx.verdict.calibration_report(bins=5)}

    @app.post("/api/verdict/evaluate")
    def evaluate() -> dict:
        if ctx.config.data_source == "mock":
            ctx.service.refresh_data(ctx.universe, ["ohlcv"])
        n = ctx.verdict.evaluate_open()
        return {"evaluated": n, "calibration": ctx.verdict.calibration_report(bins=5)}

    def _collect_bars(max_symbols: int = 60) -> dict:
        """SSOT 에 적재된 일봉을 종목별로 모은다(백테스트 입력). 너무 많으면 상한."""
        bars_by: dict = {}
        for sym in (ctx.universe or []):
            dp = ctx.ssot.get(sym, Kind.OHLCV.value)
            if dp and dp.payload.get("bars"):
                bars_by[sym] = dp.payload["bars"]
            if len(bars_by) >= max_symbols:
                break
        return bars_by

    @app.get("/api/backtest/{horizon}")
    def backtest(horizon: str, forward_days: int = 0, rebalance_every: int = 5,
                 cost_bps: float = 30.0, refresh: bool = False) -> dict:
        """점수의 예측력을 SSOT 일봉으로 백테스트. look-ahead 차단·거래비용·walk-forward 포함."""
        from app.backtest.engine import Backtester, BacktestConfig
        if horizon not in HORIZONS:
            raise HTTPException(404, f"unknown horizon: {horizon}")
        if refresh and ctx.config.data_source != "mock":
            try:
                ctx.service.refresh_data(ctx.universe, [Kind.OHLCV.value])
            except Exception:
                pass
        bars_by = _collect_bars()
        if len(bars_by) < 2:
            return {"horizon": horizon, "error": "일봉 데이터 부족",
                    "hint": "먼저 추천을 한 번 불러와(또는 refresh=true) 일봉을 적재하세요.",
                    "symbols_with_bars": len(bars_by)}
        cfg = BacktestConfig(horizon=horizon, forward_days=forward_days,
                             rebalance_every=rebalance_every, cost_bps=cost_bps)
        try:
            res = Backtester(cfg, allow_uncalibrated=True).run(bars_by)
        except Exception as e:
            return {"horizon": horizon, "error": str(e)}
        out = asdict(res)
        out["symbols_tested"] = len(bars_by)
        out["data_source"] = ctx.config.data_source
        return out

    @app.get("/api/backtest/{horizon}/calibrate")
    def backtest_calibrate(horizon: str, forward_days: int = 0) -> dict:
        """백테스트 표본으로 IC 기반 가중치를 산출(검토용 — 자동 적용하지 않음)."""
        from app.backtest.engine import Backtester, BacktestConfig
        from app.scoring.calibration import calibrate_from_samples
        from app.core.errors import NotCalibrated
        if horizon not in HORIZONS:
            raise HTTPException(404, f"unknown horizon: {horizon}")
        bars_by = _collect_bars()
        if len(bars_by) < 2:
            return {"horizon": horizon, "error": "일봉 데이터 부족"}
        bt = Backtester(BacktestConfig(horizon=horizon, forward_days=forward_days),
                        allow_uncalibrated=True)
        samples = bt.signal_samples(bars_by)
        sample_sizes = {k: len(v) for k, v in samples.items()}
        try:
            ws = calibrate_from_samples(horizon, samples)
            return {"horizon": horizon, "calibrated": True,
                    "weights": ws.weights, "source": ws.source,
                    "sample_sizes": sample_sizes,
                    "note": "검토용입니다. 적용하려면 weights.py 의 해당 호라이즌을 교체하세요(과최적화 주의)."}
        except NotCalibrated as e:
            return {"horizon": horizon, "calibrated": False, "reason": str(e),
                    "sample_sizes": sample_sizes,
                    "note": "표본 부족 또는 양의 IC 신호 없음 — 더 긴 기간/많은 종목 필요."}

    def _alert_symbols() -> list:
        """알림 대상: 관심종목(watchlist) 우선, 없으면 유니버스 상위 일부."""
        from app.config import load_watchlist
        wl = load_watchlist(ctx.config)
        return wl if wl else (ctx.universe or [])[:30]

    @app.get("/api/alerts")
    def alerts(horizon: str = "swing",
               chg_pct: float = 5.0, vol_mult: float = 2.0, score_min: float = 85.0) -> dict:
        """관심종목 알림 조건 판정. 각 알림은 (종목+종류+날짜) 고유 id 로 중복 제거 가능.
        조건: 급등/급락, 52주 신고가/신저가 근접, 거래량 급증, 점수 임계 돌파, 당일 신규 공시."""
        out = []
        today = ctx.clock.now().date().isoformat()
        syms = _alert_symbols()

        rec_scores: dict = {}
        if ctx.config.data_source != "mock":
            try:
                cached = _rec_cache.get(horizon)
                recs = cached[1] if cached else [asdict(r) for r in
                       ctx.service.recommend(horizon, top_n=100, scan_limit=SCAN)]
                for r in recs:
                    rec_scores[r["symbol"]] = r["score"]
            except Exception:
                pass

        def add(sym, name, typ, level, msg):
            out.append({"id": f"{sym}:{typ}:{today}", "symbol": sym, "name": name,
                        "type": typ, "level": level, "message": msg,
                        "ts": ctx.clock.now().isoformat()})

        for sym in syms:
            name = ctx.name_of(sym)
            dp = ctx.ssot.get(sym, Kind.OHLCV.value)
            bars = dp.payload.get("bars") if dp else None
            if bars:
                last = bars[-1]
                if len(bars) >= 2 and bars[-2]["c"] > 0:
                    chg = (last["c"] - bars[-2]["c"]) / bars[-2]["c"] * 100
                    if chg >= chg_pct:
                        add(sym, name, "급등", "up", f"{name} +{chg:.1f}% (현재 {last['c']:,.0f})")
                    elif chg <= -chg_pct:
                        add(sym, name, "급락", "down", f"{name} {chg:.1f}% (현재 {last['c']:,.0f})")
                window = bars[-252:] if len(bars) > 252 else bars
                if len(window) >= 2:
                    prior = window[:-1]                       # 전일까지의 고/저 대비 오늘 돌파
                    prior_hi = max(b["h"] for b in prior)
                    prior_lo = min(b["l"] for b in prior)
                    if last["c"] >= prior_hi:
                        add(sym, name, "52주신고가", "up", f"{name} 52주 신고가 경신 ({last['c']:,.0f})")
                    elif last["c"] <= prior_lo:
                        add(sym, name, "52주신저가", "down", f"{name} 52주 신저가 경신 ({last['c']:,.0f})")
                if len(bars) >= 21:
                    avg = sum(b["v"] for b in bars[-21:-1]) / 20
                    if avg > 0 and last["v"] >= vol_mult * avg:
                        add(sym, name, "거래량급증", "info",
                            f"{name} 거래량 {last['v']/avg:.1f}배 급증")
            sc = rec_scores.get(sym)
            if sc is not None and sc >= score_min:
                add(sym, name, "점수상위", "info", f"{name} {horizon} 점수 {sc:.0f} (상위권)")
            n = ctx.ssot.get(sym, Kind.NEWS.value)
            for it in (n.payload.get("items") if n else []) or []:
                if it.get("source", "공시") != "news" and (it.get("published_at", "")[:10] == today):
                    title = (it.get("title") or "")[:40]
                    out.append({"id": f"{sym}:공시:{hash(title) & 0xffff}:{today}",
                                "symbol": sym, "name": name, "type": "공시", "level": "info",
                                "message": f"{name} 공시: {title}", "ts": it.get("published_at", "")})

        return {"alerts": out, "ts": ctx.clock.now().isoformat(),
                "watch_count": len(syms), "data_source": ctx.config.data_source}

    def _parse_account(s: str) -> tuple:
        """'12345678-01' 또는 '1234567801' 또는 '12345678' -> (cano, prdt)."""
        s = (s or "").strip()
        if not s:
            return ("", "")
        if "-" in s:
            a, b = s.split("-", 1)
            return (a.strip(), (b.strip() or "01"))
        digits = "".join(ch for ch in s if ch.isdigit())
        if len(digits) >= 10:
            return (digits[:8], digits[8:10])
        return (digits, "01")

    @app.get("/api/holdings")
    def holdings(refresh: bool = True) -> dict:
        """KIS 실보유종목 + 각 호라이즌 점수 오버레이. 읽기 전용(매매 안 함)."""
        import random as _r
        if ctx.config.data_source == "mock":
            seed = ctx.clock.now().strftime("%Y%m%d%H")
            demo_syms = [("005930", "삼성전자", 68000), ("000660", "SK하이닉스", 180000),
                         ("035720", "카카오", 52000), ("373220", "LG에너지솔루션", 420000)]
            positions = []
            for code, name, avg in demo_syms:
                rng = _r.Random(seed + code)
                cur = round(avg * (1 + rng.uniform(-0.15, 0.20)), -1)
                qty = rng.choice([5, 10, 20, 50])
                pnl = (cur - avg) * qty
                positions.append({"symbol": code, "name": name, "qty": qty,
                                  "avg_price": avg, "cur_price": cur,
                                  "eval_amount": cur * qty, "buy_amount": avg * qty,
                                  "pnl": pnl, "pnl_pct": round((cur - avg) / avg * 100, 2),
                                  "scores": {h: {"score": round(rng.uniform(20, 95), 1),
                                                 "confidence": round(rng.uniform(0.3, 0.8), 2)}
                                             for h in HORIZONS}})
            ev = sum(p["eval_amount"] for p in positions)
            bu = sum(p["buy_amount"] for p in positions)
            return {"positions": positions, "data_source": "mock",
                    "summary": {"eval_total": ev, "buy_total": bu, "pnl_total": ev - bu,
                                "cash": 1_000_000, "pnl_pct_total": round((ev - bu) / bu * 100, 2) if bu else 0},
                    "ts": ctx.clock.now().isoformat()}

        cano, prdt = _parse_account(ctx.config.kis_account)
        if not cano or ctx.quote is None or not hasattr(ctx.quote, "holdings"):
            return {"error": "계좌 미설정", "hint": "KIS_ACCOUNT=12345678-01 형식으로 .env 에 설정하세요."}
        try:
            h = ctx.quote.holdings(cano, prdt)
        except Exception as e:
            return {"error": f"잔고조회 실패: {e}",
                    "hint": "계좌번호/권한(KIS_PAPER 실전여부)과 앱키 확인. 실전계좌는 KIS_PAPER=false."}
        positions = h["positions"]
        held = [p["symbol"] for p in positions]
        if refresh and held:
            try:
                ctx.service.refresh_data(held, [Kind.OHLCV.value, Kind.SUPPLY.value,
                                                Kind.FINANCIALS.value, Kind.NEWS.value])
            except Exception:
                pass
        # 각 호라이즌 점수(유니버스+보유 횡단면 백분위) 오버레이
        score_by: dict = {h2: {} for h2 in HORIZONS}
        for hz in HORIZONS:
            try:
                recs = ctx.service.recommend(hz, top_n=1000, scan_limit=max(SCAN, 1000))
                for r in recs:
                    score_by[hz][r.symbol] = {"score": r.score, "confidence": r.confidence,
                                              "fired": len(r.fired)}
            except Exception:
                pass
        for p in positions:
            p["scores"] = {hz: score_by[hz].get(p["symbol"]) for hz in HORIZONS}
        bu = h["summary"].get("buy_total") or sum(p["buy_amount"] for p in positions)
        ev = h["summary"].get("eval_total") or sum(p["eval_amount"] for p in positions)
        h["summary"]["pnl_pct_total"] = round((ev - bu) / bu * 100, 2) if bu else 0
        return {"positions": positions, "summary": h["summary"],
                "data_source": ctx.config.data_source, "account": f"{cano}-{prdt}",
                "ts": ctx.clock.now().isoformat()}

    @app.get("/api/themes")
    def themes(window: int = 5, horizon: str = "swing", refresh: bool = False) -> dict:
        """테마 로테이션 — 테마별 구성종목의 수익률·breadth·거래대금·평균점수 집계, 자금 흐름 순.
        window=집계 거래일(기본 5=주간). 일봉 기반."""
        from app.data.themes import THEMES, all_theme_symbols
        import statistics as _st

        if refresh and ctx.config.data_source != "mock":
            try:
                ctx.service.refresh_data(list(all_theme_symbols()), [Kind.OHLCV.value])
            except Exception:
                pass

        rec_scores: dict = {}
        cached = _rec_cache.get(horizon)
        if cached:
            for r in cached[1]:
                rec_scores[r["symbol"]] = r["score"]

        def member_metrics(sym: str):
            dp = ctx.ssot.get(sym, Kind.OHLCV.value)
            bars = dp.payload.get("bars") if dp else None
            if not bars or len(bars) < window + 1:
                return None
            c_now = bars[-1]["c"]
            c_prev = bars[-1 - window]["c"]
            c_1d = bars[-2]["c"]
            ret_w = (c_now - c_prev) / c_prev * 100 if c_prev else 0.0
            ret_1d = (c_now - c_1d) / c_1d * 100 if c_1d else 0.0
            relv = None
            if len(bars) >= 21:
                avg = sum(b["v"] for b in bars[-21:-1]) / 20
                relv = (bars[-1]["v"] / avg) if avg > 0 else None
            return {"ret_w": ret_w, "ret_1d": ret_1d, "up": ret_w > 0, "relv": relv,
                    "score": rec_scores.get(sym)}

        out = []
        for theme, syms in THEMES.items():
            ms = [m for m in (member_metrics(s) for s in syms) if m is not None]
            if not ms:
                continue
            rets = [m["ret_w"] for m in ms]
            scores = [m["score"] for m in ms if m["score"] is not None]
            relvs = [m["relv"] for m in ms if m["relv"] is not None]
            # 대표 종목(주간 수익률 상위 3)
            top = sorted(
                [{"symbol": s, "name": ctx.name_of(s), "ret_w": round(m["ret_w"], 2)}
                 for s, m in zip([s for s in syms if ctx.ssot.get(s, Kind.OHLCV.value)], ms)],
                key=lambda x: x["ret_w"], reverse=True)[:3]
            out.append({
                "theme": theme, "members": len(ms),
                "ret_w": round(_st.median(rets), 2),
                "ret_1d": round(_st.median([m["ret_1d"] for m in ms]), 2),
                "breadth": round(sum(1 for m in ms if m["up"]) / len(ms), 2),
                "rel_volume": round(_st.median(relvs), 2) if relvs else None,
                "avg_score": round(_st.mean(scores), 1) if scores else None,
                "leaders": top,
            })
        out.sort(key=lambda x: x["ret_w"], reverse=True)
        return {"themes": out, "window": window, "horizon": horizon,
                "data_source": ctx.config.data_source, "ts": ctx.clock.now().isoformat()}

    _regime_cache = {"ts": 0.0, "data": None}

    @app.get("/api/regime")
    def regime(refresh: bool = False) -> dict:
        if _md_blocked():
            return {**_MARKETDATA_DISABLED}
        """시장 국면(강세/약세/횡보) — 코스피 시계열 추세 + VIX 변동성. 10분 캐시."""
        from app.regime import detect_regime
        from dataclasses import asdict as _asdict
        import random as _r
        now_t = _time.time()
        if not refresh and _regime_cache["data"] is not None and now_t - _regime_cache["ts"] < 600:
            return _regime_cache["data"]

        if ctx.config.data_source == "mock":
            seed = ctx.clock.now().strftime("%Y%m%d")
            rng = _r.Random(seed)
            base = 2600.0
            drift = rng.choice([0.0008, -0.0009, 0.00003])   # 강세/약세/횡보 데모
            closes = [base]
            for _ in range(259):
                closes.append(closes[-1] * (1 + drift + rng.gauss(0, 0.004)))
            vix = round(rng.uniform(12, 30), 1)
            reg = detect_regime(closes, vix)
            out = _asdict(reg)
            out.update({"data_source": "mock", "ts": ctx.clock.now().isoformat(),
                        "kospi": round(closes[-1], 2)})
            _regime_cache["data"] = out
            _regime_cache["ts"] = now_t
            return out

        closes = None
        vix = None
        if ctx.market is not None:
            try:
                closes = ctx.market.fetch_index_series("^KS11", "1y")
            except Exception:
                closes = None
            try:
                for g in (ctx.market.fetch_indices() or []):
                    if g.get("name") == "VIX":
                        vix = g.get("value")
                        break
            except Exception:
                pass
        reg = detect_regime(closes, vix)
        out = _asdict(reg)
        out.update({"data_source": ctx.config.data_source, "ts": ctx.clock.now().isoformat(),
                    "kospi": round(closes[-1], 2) if closes else None,
                    "history_days": len(closes) if closes else 0})
        if not closes:
            out["note"] = "코스피 시계열을 못 받았습니다(야후 차단/네트워크). VIX·국면 정확도 제한."
        _regime_cache["data"] = out
        _regime_cache["ts"] = now_t
        return out

    @app.get("/api/anomalies")
    def anomalies(refresh: bool = False, limit: int = 30) -> dict:
        if _md_blocked():
            return {**_MARKETDATA_DISABLED, "items": []}
        """이상징후 레이더 — 종목 자기 이력 대비 거래량·수급 비정상을 z-score 로 포착, 점수 순."""
        from app.anomaly import detect_anomalies
        syms = _alert_symbols()
        if ctx.config.data_source == "mock":
            import random as _r
            seed = ctx.clock.now().strftime("%Y%m%d%H")
            samples = [("005930", "삼성전자", "외국인 순매수 급증", 0.9, 3.4),
                       ("000660", "SK하이닉스", "조용한 매집(외인+기관)", 0.75, 6.0),
                       ("035720", "카카오", "거래량 급증·가격 무반응", 0.6, 2.8),
                       ("247540", "에코프로비엠", "기관 순매도 급증", 0.5, -2.5)]
            out = []
            for code, name, label, sc, z in samples:
                rng = _r.Random(seed + code)
                if rng.random() < 0.7:
                    out.append({"symbol": code, "name": name, "score": sc,
                                "flags": [{"type": label, "label": label, "z": z,
                                           "severity": sc, "detail": f"{label} — 데모 데이터 (z={z})"}],
                                "metrics": {"vol_z": round(rng.uniform(1, 4), 1)}})
            out.sort(key=lambda x: x["score"], reverse=True)
            return {"anomalies": out, "scanned": len(syms), "has_supply": True,
                    "data_source": "mock", "ts": ctx.clock.now().isoformat()}
        if refresh and ctx.config.data_source != "mock":
            try:
                ctx.service.refresh_data(syms, [Kind.OHLCV.value, Kind.SUPPLY.value])
            except Exception:
                pass
        out = []
        for sym in syms:
            ob = ctx.ssot.get(sym, Kind.OHLCV.value)
            sb = ctx.ssot.get(sym, Kind.SUPPLY.value)
            bars = ob.payload.get("bars") if ob else None
            supply = sb.payload.get("daily") if sb else None
            res = detect_anomalies(bars, supply)
            if res["score"] > 0 and res["flags"]:
                out.append({"symbol": sym, "name": ctx.name_of(sym),
                            "score": res["score"], "flags": res["flags"],
                            "metrics": res["metrics"]})
        out.sort(key=lambda x: x["score"], reverse=True)
        has_supply = any(ctx.ssot.get(s, Kind.SUPPLY.value) for s in syms)
        return {"anomalies": out[:limit], "scanned": len(syms),
                "has_supply": bool(has_supply), "data_source": ctx.config.data_source,
                "ts": ctx.clock.now().isoformat()}

    return app
