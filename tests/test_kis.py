"""KIS provider 테스트 — 가짜 Transport 로 KIS 응답을 주입해 정규화 검증.

(네트워크 없이 응답 -> DataPoint 변환 로직을 실제로 검증한다. 라이브 호출은 사용자 환경에서.)
"""
from datetime import datetime, timezone

from app.core.errors import ProviderError
from app.data.schema import Kind
from app.providers.kis import KISProvider, HttpTransport

NOW = datetime(2026, 6, 19, 8, 0, tzinfo=timezone.utc)  # 17:00 KST

# --- KIS 응답 형태 fixture ---
TOKEN_OK = {"access_token": "TESTTOKEN", "token_type": "Bearer", "expires_in": 86400}

DAILY_OK = {"rt_cd": "0", "msg1": "정상", "output2": [
    # KIS 는 최신순으로 내려줌 (provider 가 오름차순 정렬해야 함)
    {"stck_bsop_date": "20260619", "stck_oprc": "74000", "stck_hgpr": "74800",
     "stck_lwpr": "73500", "stck_clpr": "74200", "acml_vol": "12000000"},
    {"stck_bsop_date": "20260618", "stck_oprc": "73200", "stck_hgpr": "73900",
     "stck_lwpr": "72800", "stck_clpr": "73000", "acml_vol": "9500000"},
    {"stck_bsop_date": "20260617", "stck_oprc": "72000", "stck_hgpr": "72500",
     "stck_lwpr": "71500", "stck_clpr": "72100", "acml_vol": "8800000"},
]}

INVESTOR_OK = {"rt_cd": "0", "output": [
    {"stck_bsop_date": "20260619", "frgn_ntby_qty": "150000",
     "orgn_ntby_qty": "-30000", "prsn_ntby_qty": "-120000"},
    {"stck_bsop_date": "20260618", "frgn_ntby_qty": "80000",
     "orgn_ntby_qty": "20000", "prsn_ntby_qty": "-100000"},
]}

DAILY_FAIL = {"rt_cd": "1", "msg1": "조회할 자료가 없습니다."}


class FakeTransport(HttpTransport):
    def __init__(self):
        self.post_calls = 0
        self.get_calls = 0

    def post(self, url, headers, body):
        self.post_calls += 1
        if url.endswith("/oauth2/tokenP"):
            return 200, dict(TOKEN_OK)
        return 404, {}

    def get(self, url, headers, params):
        self.get_calls += 1
        if "inquire-daily-itemchartprice" in url:
            return 200, dict(DAILY_OK)
        if "inquire-investor" in url:
            return 200, dict(INVESTOR_OK)
        return 404, {}


def _provider(transport=None):
    return KISProvider("KEY", "SECRET", paper=True, transport=transport or FakeTransport())


class _FakeTransport(HttpTransport):
    """토큰 발급 + URL 부분일치 라우팅. routes={url조각: 응답dict}."""
    def __init__(self, routes):
        self.routes = routes

    def post(self, url, headers, body):
        if url.endswith("/oauth2/tokenP"):
            return 200, dict(TOKEN_OK)
        return 404, {}

    def get(self, url, headers, params):
        for frag, resp in self.routes.items():
            if frag in url:
                return 200, dict(resp)
        return 404, {}

    def post_form(self, url, headers, data):
        return 404, {}


def test_requires_credentials():
    try:
        KISProvider("", "", paper=True)
        assert False, "should raise without creds"
    except ProviderError:
        pass


def test_token_issued_and_cached():
    t = FakeTransport()
    p = _provider(t)
    p._fetch_ohlcv("005930", NOW)
    p._fetch_supply("005930", NOW)
    assert t.post_calls == 1  # 토큰은 한 번만 발급(캐시)


def test_ohlcv_normalized_and_sorted():
    p = _provider()
    dp = p.fetch("ohlcv", "ohlcv", now=NOW)
    bars = dp.payload["bars"]
    assert [b["date"] for b in bars] == ["2026-06-17", "2026-06-18", "2026-06-19"]  # 오름차순
    assert bars[-1]["c"] == 74200.0 and bars[-1]["v"] == 12000000
    assert dp.payload["status"] == "normal"
    assert dp.as_of <= NOW            # lookahead 차단
    assert dp.fetched_at == NOW
    assert dp.source == "kis"


def test_ohlcv_as_of_clamped_to_now():
    # 최신 봉이 오늘(20260619)이고 장마감(15:30 KST=06:30 UTC)은 now(08:00 UTC)보다 과거 -> 그대로
    p = _provider()
    dp = p.fetch("ohlcv", "ohlcv", now=NOW)
    # 06:30 UTC <= 08:00 UTC 이므로 클램프 없이 장마감 시각
    assert dp.as_of.hour == 6 and dp.as_of.minute == 30


def test_supply_normalized_signs():
    p = _provider()
    dp = p.fetch("supply", "supply", now=NOW)
    daily = dp.payload["daily"]
    assert [d["date"] for d in daily] == ["2026-06-18", "2026-06-19"]
    last = daily[-1]
    assert last["foreign_net"] == 150000.0   # 외인 순매수(+)
    assert last["inst_net"] == -30000.0      # 기관 순매도(-)


def test_rt_cd_failure_raises():
    class FailT(FakeTransport):
        def get(self, url, headers, params):
            self.get_calls += 1
            return 200, dict(DAILY_FAIL)
    p = _provider(FailT())
    try:
        p.fetch("ohlcv", "ohlcv", now=NOW)
        assert False, "should raise on rt_cd!=0"
    except ProviderError as e:
        assert "rt_cd" in str(e)


def test_current_price_normalized():
    resp = {"rt_cd": "0", "output": {
        "stck_prpr": "74200", "prdy_vrss": "1500", "prdy_vrss_sign": "2",
        "prdy_ctrt": "2.06", "acml_vol": "12345678",
        "stck_oprc": "73000", "stck_hgpr": "74800", "stck_lwpr": "72800"}}
    p = KISProvider("k", "s", transport=_FakeTransport({"inquire-price": resp}))
    q = p.current_price("005930")
    assert q["price"] == 74200.0
    assert q["change"] == 1500.0 and q["change_pct"] == 2.06   # 상승(sign=2)
    assert q["volume"] == 12345678


def test_current_price_negative_sign():
    resp = {"rt_cd": "0", "output": {
        "stck_prpr": "70000", "prdy_vrss": "900", "prdy_vrss_sign": "5",  # 하락
        "prdy_ctrt": "1.27", "acml_vol": "5000"}}
    p = KISProvider("k", "s", transport=_FakeTransport({"inquire-price": resp}))
    q = p.current_price("005930")
    assert q["change"] == -900.0 and q["change_pct"] == -1.27   # 하락은 음수로
    p = _provider()
    assert p.fetch("tick", "tick", now=NOW) is None   # tick 은 WS 빌드 소관
