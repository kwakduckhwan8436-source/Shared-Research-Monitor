"""KIS provider — 한국투자증권 OpenAPI REST 실연동.

담당 kind (REST):
- ohlcv  : 일봉 (inquire-daily-itemchartprice, TR FHKST03010100)
- supply : 투자자별 순매수 (inquire-investor, TR FHKST01010900)

(tick/orderbook 은 KIS WebSocket 스트림이라 별도 빌드. 본 provider 는 REST 전용.)

설계:
- OAuth 토큰 발급/캐시 (POST /oauth2/tokenP). KIS 는 토큰 발급도 rate-limit 하므로 반드시 캐시.
- HTTP 는 Transport 추상화 뒤에 둠 -> 테스트에서 가짜 응답 주입 가능(정규화 로직 검증).
- 실패/없음 -> None 또는 ProviderError. 절대 추정값을 만들지 않는다(추정금지).
- as_of = min(데이터 기준시각, now) 로 lookahead 차단(미래 타임스탬프 금지) + 실시간성 반영.

⚠ 필드명 확인 권장: 투자자(inquire-investor) 응답의 순매수 필드명은 환경/버전에 따라
   다를 수 있습니다. 당신의 V18.2 KIS 코드와 대조해 _FIELD_* 상수만 맞추면 됩니다.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.clock import KST
from app.core.errors import ProviderError
from app.data.schema import DataPoint, Kind
from app.providers.base import DataProvider

KIS_REAL_BASE = "https://openapi.koreainvestment.com:9443"
KIS_VTS_BASE = "https://openapivts.koreainvestment.com:29443"  # 모의투자

TR_DAILY_CHART = "FHKST03010100"   # 국내주식 기간별시세(일봉)
TR_INVESTOR = "FHKST01010900"      # 국내주식 투자자별 매매동향
TR_PRICE = "FHKST01010100"         # 주식현재가 시세(실시간 조회용 REST)
TR_ASKING = "FHKST01010200"        # 주식현재가 호가/예상체결(총잔량 — 매수/매도 압력)
TR_INDEX = "FHPUP02100000"         # 국내업종 현재지수(코스피/코스닥 등)
TR_BALANCE_REAL = "TTTC8434R"      # 주식잔고조회(실전)
TR_BALANCE_PAPER = "VTTC8434R"     # 주식잔고조회(모의)

# 투자자 응답 필드명 (필요 시 V18.2 와 대조해 수정)
_F_DATE = "stck_bsop_date"
_F_FOREIGN = "frgn_ntby_qty"   # 외국인 순매수 수량
_F_INST = "orgn_ntby_qty"      # 기관 순매수 수량
_F_RETAIL = "prsn_ntby_qty"    # 개인 순매수 수량
# 세부 투자주체(있을 때만 사용 — KIS 응답에 따라 일부만 존재할 수 있음)
_F_SUB = {
    "pension": "pen_fund_ntby_qty",   # 연기금
    "trust": "ivtr_ntby_qty",         # 투신(투자신탁)
    "private": "pe_fund_ntby_qty",    # 사모펀드
    "bank": "bank_ntby_qty",          # 은행
    "insurance": "insu_ntby_qty",     # 보험
    "fin_invest": "scrt_ntby_qty",    # 금융투자(증권)
    "other_fin": "etc_ntby_qty",      # 기타금융
    "other_corp": "etc_corp_ntby_qty",  # 기타법인
}
_SUB_KO = {
    "pension": "연기금", "trust": "투신", "private": "사모", "bank": "은행",
    "insurance": "보험", "fin_invest": "금융투자", "other_fin": "기타금융",
    "other_corp": "기타법인",
}


# ----------------------------------------------------------------------------
# HTTP Transport (stdlib urllib; 테스트에서 교체 가능)
# ----------------------------------------------------------------------------
class HttpTransport:
    def get(self, url: str, headers: dict, params: dict) -> tuple[int, dict]:
        raise NotImplementedError

    def post(self, url: str, headers: dict, body: dict) -> tuple[int, dict]:
        raise NotImplementedError

    def post_form(self, url: str, headers: dict, data: dict) -> tuple[int, dict]:
        raise NotImplementedError


class UrllibTransport(HttpTransport):
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def _send(self, req: urllib.request.Request) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8"))
            except Exception:
                payload = {}
            return e.code, payload
        except urllib.error.URLError as e:
            raise ProviderError(f"network error: {e}") from e

    def get(self, url: str, headers: dict, params: dict) -> tuple[int, dict]:
        q = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{url}?{q}", headers=headers, method="GET")
        return self._send(req)

    def post(self, url: str, headers: dict, body: dict) -> tuple[int, dict]:
        h = dict(headers)
        h["content-type"] = "application/json; charset=utf-8"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=h, method="POST")
        return self._send(req)

    def post_form(self, url: str, headers: dict, data: dict) -> tuple[int, dict]:
        h = dict(headers)
        h["content-type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=h, method="POST")
        return self._send(req)

    def post_form_text(self, url: str, headers: dict, data: dict) -> tuple[int, str]:
        """폼 POST → 원시 텍스트 반환(JSON 강제 안 함). KRX 등 비정형 응답 견고 처리."""
        h = dict(headers)
        h["content-type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                # KRX는 EUC-KR/UTF-8 혼용 → 둘 다 시도
                for enc in ("utf-8", "euc-kr", "cp949"):
                    try:
                        return r.status, raw.decode(enc)
                    except Exception:
                        continue
                return r.status, raw.decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read().decode("utf-8", "ignore")
            except Exception:
                return e.code, ""
        except urllib.error.URLError as e:
            raise ProviderError(f"network error: {e}") from e


def _f(x: Any) -> float:
    """KIS 는 숫자를 문자열로 준다. 안전 파싱(빈 값/None -> 0)."""
    try:
        return float(str(x).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _norm_market(raw: str) -> str:
    """대표시장명을 KOSPI/KOSDAQ/KONEX 로 정규화."""
    s = (raw or "").upper()
    if "코스피" in raw or "KOSPI" in s or "유가증권" in raw:
        return "KOSPI"
    if "코스닥" in raw or "KOSDAQ" in s:
        return "KOSDAQ"
    if "코넥스" in raw or "KONEX" in s:
        return "KONEX"
    return ""


# ----------------------------------------------------------------------------
# Provider
# ----------------------------------------------------------------------------
class KISProvider(DataProvider):
    name = "kis"
    supported_kinds = (Kind.OHLCV.value, Kind.SUPPLY.value)

    def __init__(self, app_key: str, app_secret: str, *, paper: bool = True,
                 transport: Optional[HttpTransport] = None,
                 min_interval: float = 0.12, daily_lookback_days: int = 200):
        if not app_key or not app_secret:
            raise ProviderError("KIS app_key/app_secret 누락 (.env 설정 필요)")
        self.app_key = app_key
        self.app_secret = app_secret
        self.base = KIS_VTS_BASE if paper else KIS_REAL_BASE
        self.transport = transport or UrllibTransport()
        self.min_interval = min_interval
        self.daily_lookback_days = daily_lookback_days
        # 시장 구분 코드: "UN"=통합(KRX+NXT 넥스트레이드 합산), "J"=KRX만.
        # 거래량·거래대금을 양 거래소 합산으로 보려면 "UN"(기본).
        import os as _os
        self.mrkt_div = (_os.getenv("RECO_MARKET_DIV", "UN") or "UN").strip().upper()
        self._token: Optional[str] = None
        self._token_exp: float = 0.0
        self._last_call: float = 0.0

    # ----- rate limit -----
    def _throttle(self) -> None:
        dt = time.monotonic() - self._last_call
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last_call = time.monotonic()

    # ----- 인증 -----
    def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_exp - 120:
            return self._token
        status, body = self.transport.post(
            f"{self.base}/oauth2/tokenP",
            headers={},
            body={"grant_type": "client_credentials",
                  "appkey": self.app_key, "appsecret": self.app_secret},
        )
        token = body.get("access_token")
        if status != 200 or not token:
            raise ProviderError(f"KIS 토큰 발급 실패 (status={status}, msg={body.get('error_description') or body})")
        self._token = token
        self._token_exp = time.time() + int(body.get("expires_in", 86400))
        return token

    def _headers(self, tr_id: str) -> dict:
        return {
            "authorization": f"Bearer {self._ensure_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        self._throttle()
        status, body = self.transport.get(f"{self.base}{path}", self._headers(tr_id), params)
        if status != 200:
            raise ProviderError(f"KIS HTTP {status} ({path})")
        if str(body.get("rt_cd", "0")) != "0":
            raise ProviderError(f"KIS rt_cd={body.get('rt_cd')} {body.get('msg1','')} ({path})")
        return body

    # ----- as_of 계산 -----
    @staticmethod
    def _as_of(date_str: str, hour: int, minute: int, now: datetime) -> datetime:
        """'YYYYMMDD' + KST 시각 -> UTC, 단 now 를 넘지 않게 클램프(lookahead 차단)."""
        y, m, d = int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
        t = datetime(y, m, d, hour, minute, tzinfo=KST).astimezone(timezone.utc)
        return min(t, now)

    # ----- ohlcv -----
    def _fetch_ohlcv(self, symbol: str, now: datetime) -> DataPoint:
        end = now.astimezone(KST).date()
        start = end - timedelta(days=self.daily_lookback_days)
        body = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            TR_DAILY_CHART,
            {"FID_COND_MRKT_DIV_CODE": self.mrkt_div, "FID_INPUT_ISCD": symbol,
             "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
             "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
             "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"},
        )
        rows = body.get("output2") or []
        bars = []
        for r in rows:
            ds = r.get("stck_bsop_date")
            if not ds:
                continue
            bars.append({
                "date": f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}",
                "o": _f(r.get("stck_oprc")), "h": _f(r.get("stck_hgpr")),
                "l": _f(r.get("stck_lwpr")), "c": _f(r.get("stck_clpr")),
                "v": int(_f(r.get("acml_vol"))),
                "to": _f(r.get("acml_tr_pbmn")),     # 실제 거래대금(원)
            })
        if not bars:
            raise ProviderError(f"KIS ohlcv 데이터 없음: {symbol}")
        bars.sort(key=lambda b: b["date"])           # KIS 는 최신순 -> 오름차순 정렬
        last_date = bars[-1]["date"].replace("-", "")
        as_of = self._as_of(last_date, 15, 30, now)   # 장마감 15:30 KST
        # 대표시장(코스피/코스닥) — output1 의 rprs_mrkt_kor_name
        o1 = body.get("output1") or {}
        if isinstance(o1, list):
            o1 = o1[0] if o1 else {}
        mkt_raw = (o1.get("rprs_mrkt_kor_name") or o1.get("bstp_kor_isnm") or "").strip()
        market = _norm_market(mkt_raw)
        return DataPoint(symbol, Kind.OHLCV.value,
                         {"bars": bars, "status": "normal", "market": market},
                         as_of=as_of, fetched_at=now, source=self.name)

    # ----- supply (투자자별 순매수) -----
    def _fetch_supply(self, symbol: str, now: datetime) -> DataPoint:
        body = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            TR_INVESTOR,
            {"FID_COND_MRKT_DIV_CODE": self.mrkt_div, "FID_INPUT_ISCD": symbol},
        )
        rows = body.get("output") or body.get("output1") or []
        daily = []
        for r in rows:
            ds = r.get(_F_DATE)
            if not ds:
                continue
            rec = {
                "date": f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}",
                "foreign_net": _f(r.get(_F_FOREIGN)),
                "inst_net": _f(r.get(_F_INST)),
                "retail_net": _f(r.get(_F_RETAIL)),
            }
            # 세부 투자주체 — 응답에 해당 필드가 있을 때만 추가(없으면 생략)
            sub = {}
            for key, field in _F_SUB.items():
                if field in r and str(r.get(field)).strip() not in ("", "None"):
                    sub[key] = _f(r.get(field))
            if sub:
                rec["sub"] = sub
            daily.append(rec)
        if not daily:
            raise ProviderError(f"KIS supply 데이터 없음: {symbol}")
        daily.sort(key=lambda x: x["date"])
        last_date = daily[-1]["date"].replace("-", "")
        as_of = self._as_of(last_date, 15, 40, now)   # 장 종료 직후 집계
        return DataPoint(symbol, Kind.SUPPLY.value, {"daily": daily},
                         as_of=as_of, fetched_at=now, source=self.name)

    def current_price(self, symbol: str) -> dict:
        """실시간 현재가 조회(REST inquire-price). UI 실시간 시세 폴링용."""
        body = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            TR_PRICE,
            {"FID_COND_MRKT_DIV_CODE": self.mrkt_div, "FID_INPUT_ISCD": symbol},
        )
        o = body.get("output", {}) or {}
        sign = str(o.get("prdy_vrss_sign", "3"))
        neg = sign in ("4", "5")            # 4=하한, 5=하락
        change = _f(o.get("prdy_vrss"))
        pct = _f(o.get("prdy_ctrt"))
        return {
            "price": _f(o.get("stck_prpr")),
            "change": -abs(change) if neg else abs(change),
            "change_pct": -abs(pct) if neg else abs(pct),
            "volume": int(_f(o.get("acml_vol"))),
            "turnover": _f(o.get("acml_tr_pbmn")),   # 누적 거래대금(원) — 거래소 공식
            "strength": _f(o.get("cttr")),           # 체결강도(누적) — 100 기준 매수/매도 우위
            "market": _norm_market(o.get("rprs_mrkt_kor_name") or o.get("mrkt_div_cls_code") or ""),
            "open": _f(o.get("stck_oprc")), "high": _f(o.get("stck_hgpr")),
            "low": _f(o.get("stck_lwpr")),
        }

    def asking_price(self, symbol: str) -> dict:
        """실시간 호가 + 총잔량(매수/매도). 호가잔량 불균형 = 실시간 매수/매도 압력 지표.
        TR FHKST01010200(inquire-asking-price). 실패 시 빈 dict."""
        try:
            body = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                TR_ASKING, {"FID_COND_MRKT_DIV_CODE": self.mrkt_div, "FID_INPUT_ISCD": symbol},
            )
        except Exception:
            return {}
        o = body.get("output1", {}) or body.get("output", {}) or {}
        total_ask = _f(o.get("total_askp_rsqn"))    # 총 매도호가 잔량
        total_bid = _f(o.get("total_bidp_rsqn"))    # 총 매수호가 잔량
        bids = []; asks = []
        for i in range(1, 6):
            bp = _f(o.get(f"bidp{i}")); bq = _f(o.get(f"bidp_rsqn{i}"))
            ap = _f(o.get(f"askp{i}")); aq = _f(o.get(f"askp_rsqn{i}"))
            if bp:
                bids.append([bp, int(bq)])
            if ap:
                asks.append([ap, int(aq)])
        return {"total_ask_qty": int(total_ask), "total_bid_qty": int(total_bid),
                "bids": bids, "asks": asks}

    def index_price(self, code: str) -> dict:
        """국내 업종지수 현재가(코스피=0001, 코스닥=1001, 코스피200=2001).
        ⚠ tr_id/필드는 환경따라 다를 수 있으니 V18.2 와 대조 권장. 실패 시 호출측에서 graceful."""
        body = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            TR_INDEX,
            {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code},
        )
        o = body.get("output", {}) or {}
        sign = str(o.get("prdy_vrss_sign", "3"))
        neg = sign in ("4", "5")
        change = _f(o.get("bstp_nmix_prdy_vrss"))
        pct = _f(o.get("bstp_nmix_prdy_ctrt"))
        return {
            "value": _f(o.get("bstp_nmix_prpr")),
            "change": -abs(change) if neg else abs(change),
            "change_pct": -abs(pct) if neg else abs(pct),
        }

    @staticmethod
    def parse_holdings(body: dict) -> dict:
        """잔고조회 응답 -> {positions:[...], summary:{...}}. (파싱은 fixture 로 검증)
        output1=종목별 보유, output2=계좌 요약."""
        positions = []
        for o in (body.get("output1") or []):
            qty = int(_f(o.get("hldg_qty")))
            if qty <= 0:
                continue
            positions.append({
                "symbol": str(o.get("pdno", "")).zfill(6),
                "name": o.get("prdt_name", ""),
                "qty": qty,
                "avg_price": _f(o.get("pchs_avg_pric")),
                "cur_price": _f(o.get("prpr")),
                "eval_amount": _f(o.get("evlu_amt")),
                "buy_amount": _f(o.get("pchs_amt")),
                "pnl": _f(o.get("evlu_pfls_amt")),
                "pnl_pct": _f(o.get("evlu_pfls_rt")),
            })
        s = (body.get("output2") or [{}])
        s = s[0] if isinstance(s, list) else s
        summary = {
            "eval_total": _f(s.get("tot_evlu_amt")),
            "buy_total": _f(s.get("pchs_amt_smtl_amt")),
            "pnl_total": _f(s.get("evlu_pfls_smtl_amt")),
            "cash": _f(s.get("dnca_tot_amt")),
        }
        return {"positions": positions, "summary": summary}

    def holdings(self, cano: str, acnt_prdt_cd: str = "01") -> dict:
        """주식 보유종목 조회(읽기 전용 — 매매하지 않음). cano=종합계좌 앞 8자리."""
        tr = TR_BALANCE_PAPER if self.base == KIS_VTS_BASE else TR_BALANCE_REAL
        body = self._get(
            "/uapi/domestic-stock/v1/trading/inquire-balance", tr,
            {"CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
             "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
             "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
             "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
             "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""},
        )
        return self.parse_holdings(body)

    # ----- dispatch -----
    def fetch(self, symbol: str, kind: str, *, now: datetime) -> Optional[DataPoint]:
        if kind == Kind.OHLCV.value:
            return self._fetch_ohlcv(symbol, now)
        if kind == Kind.SUPPLY.value:
            return self._fetch_supply(symbol, now)
        return None  # tick/orderbook 등은 WS 빌드에서
