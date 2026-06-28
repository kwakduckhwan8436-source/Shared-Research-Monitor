"""추천 서비스 — 오케스트레이션.

흐름:
1. refresh_data: providers -> SSOT (필요한 kind 적재)
2. recommend(horizon): 유니버스 필터 -> 시그널 평가 -> 스코어 -> cross-sectional 정규화 -> 랭킹
3. 결과를 store 에 기록(사후 검증/캘리브레이션 근거)

멱등성: 같은 SSOT 스냅샷(snapshot_id) + 같은 가중치 -> 같은 추천(결정적 정렬).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from app.core.clock import Clock
from app.core.errors import ProviderError, NotImplementedYet
from app.core.eventbus import EventBus
from app.core.ssot import SSOT
from app.data.schema import Kind
from app.data.store import Store
from app.providers.base import DataProvider
from app.reco.universe import UniverseFilter
from app.scoring.normalize import percentile_scores
from app.scoring.scorer import HorizonScorer, ScoredSymbol
from app.scoring.weights import WeightSet, default_weights
from app.signals.base import SignalContext, SignalResult
from app.signals.registry import signals_for, required_kinds_for


@dataclass(frozen=True)
class Recommendation:
    symbol: str
    name: str
    horizon: str
    score: float                  # 0~100 (cross-sectional)
    confidence: float             # 0~1
    coverage: float
    risk_flags: list[str]
    fired: list[dict]             # [{name, value, confidence, evidence}]
    abstained: list[dict]         # [{name, abstain_reason}]
    weights_calibrated: bool
    weights_source: str
    ref_price: Optional[float]
    snapshot_id: str
    generated_at: str

    def to_store_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id, "symbol": self.symbol,
            "horizon": self.horizon, "score": self.score, "confidence": self.confidence,
            "weights_calibrated": self.weights_calibrated,
            "weights_source": self.weights_source, "risk_flags": self.risk_flags,
            "evidence": {"fired": self.fired, "abstained": self.abstained,
                         "coverage": self.coverage},
            "ref_price": self.ref_price, "generated_at": self.generated_at,
        }


def _ref_price(ssot: SSOT, symbol: str) -> Optional[float]:
    dp = ssot.get(symbol, Kind.OHLCV.value)
    if dp is None:
        return None
    bars = dp.payload.get("bars", [])
    return bars[-1]["c"] if bars else None


class RecommendationService:
    def __init__(self, ssot: SSOT, provider: DataProvider, store: Store, clock: Clock,
                 *, name_resolver=None, bus: Optional[EventBus] = None,
                 allow_uncalibrated: bool = False,
                 universe_filter: Optional[UniverseFilter] = None,
                 weights_override: Optional[dict[str, WeightSet]] = None,
                 market_map: Optional[dict] = None):
        self.ssot = ssot
        self.provider = provider
        self.store = store
        self.clock = clock
        self.bus = bus
        self.allow_uncalibrated = allow_uncalibrated
        self.uf = universe_filter or UniverseFilter()
        self._name_resolver = name_resolver or (lambda s: s)
        self._weights_override = weights_override or {}
        self._market_map = market_map or {}     # 종목코드 -> KOSPI/KOSDAQ (KRX 전종목)

    def _weights(self, horizon: str) -> WeightSet:
        return self._weights_override.get(horizon, default_weights(horizon))

    def refresh_data(self, symbols: list[str], kinds: list[str]) -> int:
        now = self.clock.now()
        loaded = 0
        skipped = 0
        for sym in symbols:
            for kind in kinds:
                try:
                    dp = self.provider.fetch(sym, kind, now=now)
                except (ProviderError, NotImplementedYet) as e:
                    # 데이터 불가(미구현 provider/조회 실패)는 추정으로 메우지 않고 건너뛴다.
                    # -> 해당 시그널은 자연히 abstain. (예상치 못한 예외는 그대로 전파)
                    skipped += 1
                    if self.bus:
                        self.bus.publish("data.unavailable",
                                         {"symbol": sym, "kind": kind, "reason": str(e)})
                    continue
                if dp is not None:
                    self.ssot.put(dp)
                    loaded += 1
        if self.bus:
            self.bus.publish("data.refreshed",
                             {"symbols": symbols, "kinds": kinds,
                              "loaded": loaded, "skipped": skipped})
        return loaded

    def diagnose(self, horizon: str, symbol: str) -> dict:
        """추천 점수의 계산 과정을 그대로 노출(검증/감사용).

        각 시그널: 필요 데이터 존재여부 -> 값 -> 가중치 -> 가중기여도,
        그리고 raw_score / confidence 의 재구성 식과 수치.
        """
        now = self.clock.now()
        ws = self._weights(horizon)
        sigs = signals_for(horizon)
        ctx = SignalContext(symbol, self.ssot, horizon, now)
        results = [s.run(ctx) for s in sigs]

        passes, reason = self.uf.passes(self.ssot, symbol, now)

        sig_trace = []
        for s, r in zip(sigs, results):
            data_present = {k: (self.ssot.get(symbol, k) is not None)
                            for k in getattr(s, "required_kinds", ())}
            w = ws.weight_of(r.name)
            contrib = (w * r.value * r.confidence) if r.fired else 0.0
            sig_trace.append({
                "name": r.name, "fired": r.fired, "value": r.value,
                "confidence": r.confidence, "weight": w,
                "weighted_contribution": round(contrib, 4),
                "evidence": r.evidence, "abstain_reason": r.abstain_reason,
                "required_data": data_present,
            })

        scorer = HorizonScorer(ws, allow_uncalibrated=self.allow_uncalibrated)
        ss = scorer.score(results)
        if ss is None:
            score_math = {"result": "추천 불가 (발화 시그널 0개 -> abstain, 0점 처리 안 함)"}
            conf_math = None
        else:
            fired = [r for r in results if r.fired]
            num = sum(ws.weight_of(r.name) * r.value * r.confidence for r in fired)
            den = sum(ws.weight_of(r.name) * r.confidence for r in fired)
            score_math = {
                "formula": "raw_score = Σ(weight·value·confidence) / Σ(weight·confidence)  [발화분만]",
                "numerator": round(num, 4), "denominator": round(den, 4),
                "raw_score_0_1": ss.raw_score,
                "note": "최종 표시 점수는 유니버스 내 cross-sectional 백분위(0~100)로 환산",
            }
            conf_math = {
                "formula": "confidence = coverage × agreement × freshness",
                "coverage": ss.coverage, "agreement": ss.agreement,
                "freshness": ss.freshness, "confidence": ss.confidence,
                "coverage_def": "발화 시그널 가중치합 / 전체 가중치합",
                "agreement_def": "1 − 2×표준편차(시그널 값들)  (합의도)",
                "freshness_def": "발화 시그널 confidence 평균",
            }

        return {
            "symbol": symbol, "name": self._name_resolver(symbol), "horizon": horizon,
            "in_universe": passes, "universe_reason": reason,
            "weights_source": ws.source, "weights_calibrated": ws.calibrated,
            "weights": ws.weights,
            "signals": sig_trace,
            "score_math": score_math, "confidence_math": conf_math,
            "snapshot_id": self.ssot.snapshot_id(),
        }

    def _turnover(self, symbol: str) -> float:
        """거래대금(원). 실제 거래대금(bar['to'])이 있으면 그것을, 없으면 종가×거래량."""
        dp = self.ssot.get(symbol, Kind.OHLCV.value)
        if dp is None:
            return 0.0
        bars = dp.payload.get("bars", [])
        if not bars:
            return 0.0
        b = bars[-1]
        to = b.get("to")
        if to:                       # KIS 실제 거래대금
            return float(to)
        return float(b["c"]) * float(b.get("v", 0))   # 폴백(근사)

    def _volume(self, symbol: str) -> float:
        """최근 거래량(주식 수)."""
        bars = self._bars(symbol)
        return float(bars[-1].get("v", 0)) if bars else 0.0

    def _market(self, symbol: str) -> str:
        """종목의 시장(KOSPI/KOSDAQ). 1순위 OHLCV payload, 2순위 KRX 전종목맵, 3순위 내장 목록."""
        dp = self.ssot.get(symbol, Kind.OHLCV.value)
        m = (dp.payload.get("market", "") if dp else "") or ""
        if m:
            return m
        km = self._market_map.get(symbol)
        if km:
            return km
        try:
            from app.providers.mock import market_of as _mkt_of
            return _mkt_of(symbol)
        except Exception:
            return ""

    def _bars(self, symbol: str) -> list:
        dp = self.ssot.get(symbol, Kind.OHLCV.value)
        return (dp.payload.get("bars", []) if dp else []) or []

    def _change_pct(self, symbol: str) -> Optional[float]:
        """직전 종가 대비 최근 종가 등락률(%). 데이터 부족이면 None."""
        bars = self._bars(symbol)
        if len(bars) < 2:
            return None
        prev, last = bars[-2]["c"], bars[-1]["c"]
        if not prev:
            return None
        return (last - prev) / prev * 100.0

    def _vol_ratio(self, symbol: str, window: int = 20) -> Optional[float]:
        """최근 거래량 / 직전 window 평균 거래량. 급증 판별."""
        bars = self._bars(symbol)
        if len(bars) < 3:
            return None
        vols = [b.get("v", 0) for b in bars[-(window + 1):-1]]
        vols = [v for v in vols if v > 0]
        if not vols:
            return None
        avg = sum(vols) / len(vols)
        if avg <= 0:
            return None
        return bars[-1].get("v", 0) / avg

    def theme_money_flow(self, rows: list[dict], top_n: int = 5,
                         by: str = "turnover") -> list[dict]:
        """스크리너 결과를 테마별로 묶어 집중도를 본다. by='turnover'(거래대금)|'net_buy'(순매수).
        '오늘은 반도체·방산에 돈이 몰림/외국인이 산다' 요약용. 주도주는 기여도(by값) 큰 순."""
        from app.data.themes import themes_for
        agg: dict[str, dict] = {}
        for r in rows:
            val = r.get(by) or 0
            if val <= 0:
                continue
            for th in themes_for(r["symbol"]):
                a = agg.setdefault(th, {"theme": th, "turnover": 0.0, "count": 0,
                                        "members": []})
                a["turnover"] += val
                a["count"] += 1
                a["members"].append({"symbol": r["symbol"], "name": r["name"],
                                     "change_pct": r.get("change_pct"), "val": val})
        for a in agg.values():
            # 주도주 = 기여도(순매수/거래대금) 큰 순 상위 3
            a["members"].sort(key=lambda m: m["val"], reverse=True)
            a["leaders"] = [{"symbol": m["symbol"], "name": m["name"],
                             "change_pct": m["change_pct"]} for m in a["members"][:3]]
            a.pop("members", None)
        out = sorted(agg.values(), key=lambda x: x["turnover"], reverse=True)
        return out[:top_n]

    def _net_buy(self, symbol: str, who: str = "foreign", days: int = 1):
        """최근 N일 외국인/기관 순매수 합(억). who: 'foreign'|'inst'. 없으면 None."""
        dp = self.ssot.get(symbol, Kind.SUPPLY.value)
        if dp is None:
            return None
        daily = dp.payload.get("daily", []) or []
        if not daily:
            return None
        key = "foreign_net" if who == "foreign" else "inst_net"
        recent = daily[-days:]
        vals = [d.get(key) for d in recent if d.get(key) is not None]
        if not vals:
            return None
        return float(sum(vals))

    def _net_buy_streak(self, symbol: str, who: str = "foreign", maxd: int = 10) -> int:
        """연속 순매수 일수(양수). 수급 강도 참고."""
        dp = self.ssot.get(symbol, Kind.SUPPLY.value)
        if dp is None:
            return 0
        daily = dp.payload.get("daily", []) or []
        key = "foreign_net" if who == "foreign" else "inst_net"
        streak = 0
        for d in reversed(daily[-maxd:]):
            v = d.get(key)
            if v is not None and v > 0:
                streak += 1
            else:
                break
        return streak

    def screen(self, mode: str = "foreign", *, top_n: int = 100, q: str = "",
               market: str = "", scan_limit: Optional[int] = None,
               search_pool: Optional[list] = None,
               cond_streak: int = 0, cond_high: bool = False,
               cond_align: bool = False) -> list[dict]:
        """수급 스크리너 — foreign=외국인 순매수 상위, inst=기관 순매수 상위.
        market='KOSPI'|'KOSDAQ' 면 해당 시장만. q=검색(전체 유니버스 대상, 미적재 종목은 즉시 적재).
        복합 조건(검색 아닐 때만): cond_streak=N(연속 순매수 N일 이상),
        cond_high=신고가(최근 종가가 60일 최고), cond_align=정배열(MA5>20>60)."""
        ql = (q or "").strip().lower()
        if ql:
            # 검색: 전체 유니버스(전 상장종목)에서 코드/이름 매칭 → 미적재면 즉시 적재.
            # 검색 중에는 시장 필터(코스피/코스닥)를 적용하지 않는다(분류 미적재여도 찾게).
            pool = search_pool or self.ssot.symbols()
            qcode = ql.replace(" ", "")
            def _match(sym: str) -> bool:
                if qcode and qcode in sym.lower():
                    return True                                  # 코드 부분일치
                nm = (self._name_resolver(sym) or "").lower().replace(" ", "")
                return bool(qcode) and qcode in nm               # 이름 부분일치(공백 무시)
            matched = [s for s in pool if _match(s)]
            # 매칭 종목 중 미적재분을 즉시 적재 — 단, 응답 지연 방지 위해 소수(8종)만 동기 적재.
            # 나머지는 이름/코드만으로 즉시 표시(클릭 시 상세에서 전체 적재).
            to_load = [s for s in matched if self.ssot.get(s, Kind.OHLCV.value) is None][:8]
            if to_load:
                try:
                    self.refresh_data(to_load, [Kind.OHLCV.value, Kind.SUPPLY.value])
                except Exception:
                    pass
            universe = matched[:60]                           # 너무 많으면 상위 60종까지만
        else:
            universe = self.uf.screen_filter(self.ssot, self.ssot.symbols(), min_turnover=0.0)
            mkt = (market or "").upper()
            if mkt in ("KOSPI", "KOSDAQ"):
                universe = [s for s in universe if self._market(s) == mkt]

        who = "inst" if mode == "inst" else "foreign"
        searching = bool(ql)
        rows: list[dict] = []
        for sym in universe:
            net = self._net_buy(sym, who, days=1)
            # 일반 랭킹은 수급 있는 종목만. 검색 중엔 수급 없어도 찾게 표시.
            if net is None and not searching:
                continue
            net5 = self._net_buy(sym, who, days=5)
            streak = self._net_buy_streak(sym, who)
            turnover = self._turnover(sym)
            # 수급 종합 점수 — 당일 + 5일누적 + 연속일 모멘텀 + 거래대금 대비 강도
            supply_score, intensity = self._supply_score(net, net5, streak, turnover)
            rows.append({
                "symbol": sym,
                "name": self._name_resolver(sym),
                "change_pct": self._change_pct(sym),
                "turnover": turnover,
                "volume": self._volume(sym),
                "net_buy": net,                                  # 당일 순매수(억) — 없으면 None
                "net_buy5": net5,                                # 5일 누적(억)
                "streak": streak,                                # 연속 순매수일
                "intensity": intensity,                          # 순매수/거래대금 (%)
                "supply_score": supply_score,                    # 종합 수급 점수
                "who": who,
                "market": self._market(sym),                     # KOSPI/KOSDAQ
                "ref_price": _ref_price(self.ssot, sym),
            })
        if searching:
            # 검색: 수급 점수 큰 순, 수급 없으면 거래대금 순으로 뒤에
            rows.sort(key=lambda r: (r["net_buy"] is not None,
                                     r.get("supply_score") or 0,
                                     r["turnover"]), reverse=True)
        else:
            # 복합 조건 필터(연속 순매수 / 신고가 / 정배열) — 검색 아닐 때만
            if cond_streak or cond_high or cond_align:
                filtered = []
                for r in rows:
                    if cond_streak and (r.get("streak") or 0) < cond_streak:
                        continue
                    if cond_high and not self._is_near_high(r["symbol"]):
                        continue
                    if cond_align and not self._is_aligned(r["symbol"]):
                        continue
                    filtered.append(r)
                rows = filtered
            # 일반: 종합 수급 점수 순(당일·5일·연속·강도 반영)
            rows.sort(key=lambda r: (r.get("supply_score") if r.get("supply_score") is not None else -1e18),
                      reverse=True)
        return rows[:top_n]

    def _is_near_high(self, symbol: str, window: int = 60) -> bool:
        """최근 종가가 window일 최고가 부근(신고가)인지. 종가가 기간 최고의 99% 이상."""
        dp = self.ssot.get(symbol, Kind.OHLCV.value)
        if not dp:
            return False
        bars = dp.payload.get("bars", [])
        if len(bars) < 5:
            return False
        seg = [b.get("c") for b in bars[-window:] if b.get("c")]
        if not seg:
            return False
        return seg[-1] >= max(seg) * 0.99

    def _is_aligned(self, symbol: str) -> bool:
        """정배열(MA5 > MA20 > MA60)인지."""
        dp = self.ssot.get(symbol, Kind.OHLCV.value)
        if not dp:
            return False
        c = [b.get("c") for b in dp.payload.get("bars", []) if b.get("c")]
        if len(c) < 60:
            return False
        def sma(w):
            return sum(c[-w:]) / w
        return sma(5) > sma(20) > sma(60)

    def _supply_score(self, net, net5, streak, turnover):
        """수급 종합 점수 + 강도. 당일 순매수를 기본으로, 5일 누적·연속일·거래대금 대비 강도로 보정.
        반환: (score 또는 None, intensity%(순매수/거래대금) 또는 None)."""
        if net is None:
            return None, None
        # 거래대금 대비 순매수 강도(%) — 작은 종목에 큰 순매수가 들어오면 더 의미 있음
        intensity = None
        to_eok = (turnover or 0) / 1e8     # 원 → 억
        if to_eok > 0:
            intensity = round(abs(net) / to_eok * 100, 2)
        # 기본 점수 = 당일 순매수(억)
        score = float(net)
        # 5일 누적이 같은 방향이면 가산(지속성) — 누적의 30% 가중
        if net5 is not None and (net5 > 0) == (net > 0):
            score += 0.3 * net5
        # 연속 순매수일 모멘텀 — 하루당 8% 가산(최대 5일=40%)
        if streak and net > 0:
            score *= (1 + min(streak, 5) * 0.08)
        # 강도 보너스 — 거래대금 대비 순매수가 강하면 가산(최대 +30%)
        if intensity is not None and net > 0:
            score *= (1 + min(intensity, 15) / 50.0)
        return round(score, 2), intensity

    def recommend(self, horizon: str, *, top_n: int = 10,
                  persist: bool = False, scan_limit: Optional[int] = None) -> list[Recommendation]:
        now = self.clock.now()
        snapshot_id = self.ssot.snapshot_id()
        scorer = HorizonScorer(self._weights(horizon),
                               allow_uncalibrated=self.allow_uncalibrated)
        sigs = signals_for(horizon)

        candidates = self.uf.filter(self.ssot, self.ssot.symbols(), now)
        # 전종목 스캔 처리량 제어: 거래대금 상위 scan_limit 만 시그널 계산 대상으로.
        if scan_limit and len(candidates) > scan_limit:
            candidates = sorted(candidates, key=self._turnover, reverse=True)[:scan_limit]
        scored: dict[str, ScoredSymbol] = {}
        for sym in candidates:
            ctx = SignalContext(sym, self.ssot, horizon, now)
            results: list[SignalResult] = [s.run(ctx) for s in sigs]
            ss = scorer.score(results)
            if ss is not None:
                scored[sym] = ss

        raw_by_symbol = {sym: ss.raw_score for sym, ss in scored.items()}
        pct = percentile_scores(raw_by_symbol)

        recs: list[Recommendation] = []
        for sym, ss in scored.items():
            recs.append(Recommendation(
                symbol=sym, name=self._name_resolver(sym), horizon=horizon,
                score=pct[sym], confidence=ss.confidence, coverage=ss.coverage,
                risk_flags=ss.risk_flags,
                fired=[{"name": r.name, "value": r.value, "confidence": r.confidence,
                        "evidence": r.evidence} for r in ss.fired],
                abstained=[{"name": r.name, "abstain_reason": r.abstain_reason}
                           for r in ss.abstained],
                weights_calibrated=ss.weights_calibrated, weights_source=ss.weights_source,
                ref_price=_ref_price(self.ssot, sym),
                snapshot_id=snapshot_id, generated_at=now.isoformat(),
            ))

        # 결정적 정렬: 점수 desc, 신뢰도 desc, 심볼 asc (멱등성)
        recs.sort(key=lambda r: (-r.score, -r.confidence, r.symbol))
        recs = recs[:top_n]

        if persist:
            for r in recs:
                self.store.save_recommendation(r.to_store_dict())
        if self.bus:
            self.bus.publish("reco.generated",
                             {"horizon": horizon, "count": len(recs),
                              "snapshot_id": snapshot_id})
        return recs
