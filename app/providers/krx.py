"""KRX provider — KRX 정보데이터시스템(data.krx.co.kr) 공매도 잔고.

담당 kind: short (공매도 잔고 비중 + 추세). RiskFlags 시그널을 보강한다(잔고비중>5% -> 리스크 플래그).

KRX 는 종목 6자리코드가 아닌 ISIN(KR7...)으로 조회한다. ISIN 체크디지트는 결정적으로
계산 가능하므로 6자리코드에서 유도한다(isin_from_code). 매핑 파일 불필요.

핵심(lookahead): 공매도 잔고는 T+? 공개 시차가 있다. as_of = 데이터의 최신 거래일(클램프 <= now).

⚠ KRX getJsonData 의 bld 코드/응답 필드는 환경에 따라 다를 수 있습니다(Referer 헤더 필요할 수 있음).
   bld·필드 매핑(parse)은 fixture 로 검증되어 있으니, 실제 응답과 한 번 대조하세요.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.core.clock import KST
from app.core.errors import ProviderError
from app.data.schema import DataPoint, Kind
from app.providers.base import DataProvider
from app.providers.kis import UrllibTransport, HttpTransport, _f

KRX_URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
# 개별종목 공매도 잔고 추이 (확인 권장)
BLD_SHORT_BALANCE = "dbms/MDC/STAT/srt/MDCSTAT30501"
# 전종목 기본정보(상장종목 코드·이름·시장)
BLD_STOCK_LIST = "dbms/MDC/STAT/standard/MDCSTAT01901"
# 전종목 ETF 목록
BLD_ETF_LIST = "dbms/MDC/STAT/standard/MDCSTAT04601"
# 개별종목 투자자별 거래실적(순매수) — 연기금·투신·사모 등 세부
BLD_INVESTOR_DETAIL = "dbms/MDC/STAT/standard/MDCSTAT02401"

# KRX 투자자 컬럼명 → 표준 키
_KRX_INV_MAP = {
    "금융투자": "fin_invest", "보험": "insurance", "투신": "trust",
    "사모": "private", "은행": "bank", "기타금융": "other_fin",
    "연기금": "pension", "연기금등": "pension",
    "기타법인": "other_corp", "개인": "retail", "외국인": "foreign",
    "기타외국인": "foreign_etc", "기관합계": "inst",
}


def parse_investor_detail(body: dict) -> dict:
    """KRX 개별종목 투자자별 순매수 응답 → {표준키: 순매수합(억)}.
    응답은 투자자명(INVST_TP_NM)별 행 + 순매수금액(NETBID_TRDVAL)."""
    rows = body.get("output") or body.get("OutBlock_1") or body.get("output1") or []
    agg: dict = {}
    for r in rows:
        inv = (r.get("INVST_TP_NM") or r.get("invst_tp_nm") or "").strip()
        if not inv:
            continue
        key = _KRX_INV_MAP.get(inv.replace(" ", "")) or _KRX_INV_MAP.get(inv)
        if not key:
            continue
        val = r.get("NETBID_TRDVAL") or r.get("netbid_trdval") or r.get("TRDVAL") or 0
        try:
            v = float(str(val).replace(",", "").strip() or 0) / 1e8   # 원→억
        except (TypeError, ValueError):
            v = 0.0
        agg[key] = agg.get(key, 0.0) + v
    return agg


def fetch_investor_detail(symbol: str, days: int = 5, transport=None) -> dict:
    """개별종목 투자자별 세부 순매수(연기금·투신·사모 등). 실패 시 빈 dict."""
    import json as _json
    from datetime import timedelta
    t = transport or UrllibTransport()
    isin = isin_from_code(symbol)
    now = datetime.now(timezone.utc).astimezone(KST)
    end = now.date(); start = end - timedelta(days=days + 7)
    headers = {
        "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }
    for scheme in ("http", "https"):
        url = f"{scheme}://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
        try:
            status, text = t.post_form_text(url, headers, {
                "bld": BLD_INVESTOR_DETAIL, "isuCd": isin,
                "strtDd": start.strftime("%Y%m%d"), "endDd": end.strftime("%Y%m%d"),
                "askBid": "3", "trdVolVal": "2", "detailView": "1",
            })
            if status == 200 and text and text.lstrip().startswith("{"):
                d = parse_investor_detail(_json.loads(text))
                if d:
                    return d
        except Exception:
            continue
    return {}


def parse_etf_list(body: dict) -> list[tuple]:
    """KRX ETF 응답 → [(code, name, 'ETF')]. ISU_SRT_CD/ISU_ABBRV."""
    rows = body.get("output") or body.get("OutBlock_1") or body.get("block1") or []
    out: list[tuple] = []
    for r in rows:
        code = (r.get("ISU_SRT_CD") or r.get("isu_srt_cd") or "").strip()
        name = (r.get("ISU_ABBRV") or r.get("ISU_NM") or "").strip()
        if code and len(code) == 6 and code.isdigit():
            out.append((code, name, "ETF"))
    return out


def fetch_etf_list(transport=None) -> list[tuple]:
    """KRX 정보데이터시스템에서 전체 ETF 목록을 가져온다(키 불필요). 실패 시 빈 리스트."""
    import json as _json
    t = transport or UrllibTransport()
    headers = {
        "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    for scheme in ("http", "https"):
        url = f"{scheme}://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
        try:
            status, text = t.post_form_text(url, headers, {
                "bld": BLD_ETF_LIST, "share": "1", "csvxls_isNo": "false",
            })
            if status == 200 and text and text.lstrip().startswith("{"):
                return parse_etf_list(_json.loads(text))
        except Exception:
            continue
    return []


def parse_stock_list(body: dict) -> list[tuple]:
    """KRX 전종목 응답 → [(code, name, market)] 리스트. market=KOSPI|KOSDAQ.
    응답 필드(예): ISU_SRT_CD(단축코드), ISU_ABBRV(약식명), MKT_TP_NM(시장구분)."""
    rows = body.get("OutBlock_1") or body.get("output") or body.get("block1") or []
    out: list[tuple] = []
    for r in rows:
        code = (r.get("ISU_SRT_CD") or r.get("isu_srt_cd") or "").strip()
        name = (r.get("ISU_ABBRV") or r.get("ISU_NM") or "").strip()
        mkt_raw = (r.get("MKT_TP_NM") or r.get("MKT_NM") or "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            continue
        if "KOSDAQ" in mkt_raw.upper() or "코스닥" in mkt_raw:
            mkt = "KOSDAQ"
        elif "KOSPI" in mkt_raw.upper() or "유가" in mkt_raw or "코스피" in mkt_raw:
            mkt = "KOSPI"
        else:
            mkt = ""
        out.append((code, name, mkt))
    return out


def fetch_stock_list(transport=None) -> list[tuple]:
    """KRX 정보데이터시스템에서 전체 상장종목 목록을 가져온다(키 불필요).
    여러 경로를 순서대로 시도하고, 실패 시 빈 리스트 반환(호출측 폴백).
    경로: ① getJsonData.cmd(JSON, 텍스트로 받아 견고 파싱) ② OTP CSV 다운로드.
    """
    import json as _json
    t = transport or UrllibTransport()
    headers = {
        "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    all_rows: list[tuple] = []

    # ── 경로 1: getJsonData.cmd (텍스트로 받아 JSON 파싱) ──
    for scheme in ("http", "https"):
        url = f"{scheme}://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
        got = []
        for mkt_id in ("STK", "KSQ"):   # STK=코스피, KSQ=코스닥
            try:
                status, text = t.post_form_text(url, headers, {
                    "bld": BLD_STOCK_LIST, "mktId": mkt_id,
                    "share": "1", "csvxls_isNo": "false",
                })
                if status == 200 and text and text.lstrip().startswith("{"):
                    body = _json.loads(text)
                    got.extend(parse_stock_list(body))
            except Exception:
                continue
        if got:
            return got

    # ── 경로 2: OTP CSV 다운로드(generate.cmd → download.cmd) ──
    otp_url_gen = "http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
    otp_url_dl = "http://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"
    for mkt_id in ("STK", "KSQ"):
        try:
            status, otp = t.post_form_text(otp_url_gen, headers, {
                "locale": "ko_KR", "mktId": mkt_id,
                "share": "1", "csvxls_isNo": "false",
                "name": "fileDown", "url": BLD_STOCK_LIST,
            })
            if status != 200 or not otp or len(otp) < 10:
                continue
            dl_headers = dict(headers)
            dl_headers["Referer"] = "http://data.krx.co.kr/"
            status2, csv_text = t.post_form_text(otp_url_dl, dl_headers, {"code": otp.strip()})
            if status2 == 200 and csv_text:
                all_rows.extend(_parse_stock_csv(csv_text))
        except Exception:
            continue
    return all_rows


def _parse_stock_csv(text: str) -> list[tuple]:
    """KRX 전종목 CSV → [(code, name, market)]. 헤더에서 컬럼 위치 자동 탐지."""
    import csv as _csv
    import io as _io
    out: list[tuple] = []
    try:
        reader = list(_csv.reader(_io.StringIO(text)))
    except Exception:
        return out
    if not reader:
        return out
    header = reader[0]
    # 컬럼 위치 탐지(단축코드/한글종목약명/시장구분)
    def col(*names):
        for i, h in enumerate(header):
            hh = h.replace(" ", "")
            if any(n in hh for n in names):
                return i
        return -1
    ci = col("단축코드", "종목코드", "코드")
    ni = col("한글종목약명", "한글종목명", "종목명")
    mi = col("시장구분", "시장")
    if ci < 0:
        return out
    for row in reader[1:]:
        if len(row) <= ci:
            continue
        code = row[ci].strip().strip('"').zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        name = row[ni].strip().strip('"') if 0 <= ni < len(row) else ""
        mkt_raw = row[mi].strip() if 0 <= mi < len(row) else ""
        if "코스닥" in mkt_raw or "KOSDAQ" in mkt_raw.upper():
            mkt = "KOSDAQ"
        elif "코스피" in mkt_raw or "유가" in mkt_raw or "KOSPI" in mkt_raw.upper():
            mkt = "KOSPI"
        else:
            mkt = ""
        out.append((code, name, mkt))
    return out


def _legacy_fetch_stock_list(transport=None) -> list[tuple]:
    """이전 단순 구현(참고용 보존)."""
    t = transport or UrllibTransport()
    headers = {"Referer": "http://data.krx.co.kr/", "User-Agent": "Mozilla/5.0 (stock-reco)"}
    all_rows: list[tuple] = []
    for mkt_id in ("STK", "KSQ"):
        try:
            status, body = t.post_form(KRX_URL, headers, {
                "bld": BLD_STOCK_LIST, "mktId": mkt_id, "share": "1", "csvxls_isNo": "false",
            })
            if status == 200:
                all_rows.extend(parse_stock_list(body))
        except Exception:
            continue
    return all_rows


def isin_from_code(code6: str) -> str:
    """KOSPI/KOSDAQ 보통주 6자리코드 -> ISIN(KR7+코드+00+체크디지트)."""
    code6 = code6.strip()
    body = "KR7" + code6 + "00"          # 보통주 NSIN: '7'+코드+'00' (= KR 다음 9자리)
    # 영문 -> 숫자(A=10..Z=35) 확장
    s = "".join(c if c.isdigit() else str(ord(c) - 55) for c in body)
    # Luhn: 우측부터 한 칸 건너 2배 (체크디지트는 맨 우측에 붙을 자리)
    total = 0
    for i, ch in enumerate(reversed(s)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - (total % 10)) % 10
    return body + str(check)


def _parse_short(rows: list[dict], now: datetime) -> tuple[float, str, datetime]:
    """공매도 잔고 행들 -> (최신 잔고비중%, 추세, as_of). 행 없으면 ValueError."""
    parsed = []
    for r in rows:
        dd = (r.get("TRD_DD") or r.get("trdDd") or "").replace("/", "").replace("-", "")
        if len(dd) != 8:
            continue
        # 잔고비중: BAL_RTO 우선, 없으면 잔고수량/상장주식수
        ratio = r.get("BAL_RTO")
        if ratio in (None, "", "-"):
            qty = _f(r.get("BAL_QTY"))
            shrs = _f(r.get("LIST_SHRS"))
            ratio_v = (qty / shrs * 100.0) if shrs else None
        else:
            ratio_v = _f(ratio)
        if ratio_v is None:
            continue
        parsed.append((dd, ratio_v))
    if not parsed:
        raise ValueError("no short rows")
    parsed.sort(key=lambda x: x[0])
    latest_dd, latest_ratio = parsed[-1]
    first_ratio = parsed[0][1]
    if latest_ratio > first_ratio * 1.05:
        trend = "up"
    elif latest_ratio < first_ratio * 0.95:
        trend = "down"
    else:
        trend = "flat"
    y, m, d = int(latest_dd[:4]), int(latest_dd[4:6]), int(latest_dd[6:8])
    as_of = min(datetime(y, m, d, 18, 0, tzinfo=KST).astimezone(timezone.utc), now)
    return round(latest_ratio, 3), trend, as_of


class KRXProvider(DataProvider):
    name = "krx"
    supported_kinds = (Kind.SHORT.value,)

    def __init__(self, *, transport: Optional[HttpTransport] = None,
                 bld: str = BLD_SHORT_BALANCE, lookback_days: int = 30):
        self.transport = transport or UrllibTransport()
        self.bld = bld
        self.lookback_days = lookback_days

    def fetch(self, symbol: str, kind: str, *, now: datetime) -> Optional[DataPoint]:
        if kind != Kind.SHORT.value:
            return None
        isin = isin_from_code(symbol)
        from datetime import timedelta
        end = now.astimezone(KST).date()
        start = end - timedelta(days=self.lookback_days)
        headers = {"Referer": "http://data.krx.co.kr/",
                   "User-Agent": "Mozilla/5.0 (stock-reco)"}
        status, body = self.transport.post_form(KRX_URL, headers, {
            "bld": self.bld, "isuCd": isin,
            "strtDd": start.strftime("%Y%m%d"), "endDd": end.strftime("%Y%m%d"),
        })
        if status != 200:
            raise ProviderError(f"KRX HTTP {status}")
        rows = body.get("OutBlock_1") or body.get("output") or body.get("block1") or []
        try:
            ratio, trend, as_of = _parse_short(rows, now)
        except ValueError:
            raise ProviderError(f"KRX 공매도 데이터 없음: {symbol}")
        return DataPoint(symbol, Kind.SHORT.value,
                         {"short_balance_ratio": ratio, "trend": trend},
                         as_of=as_of, fetched_at=now, source=self.name)
