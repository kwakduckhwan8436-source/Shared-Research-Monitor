"""KRX provider 테스트 — ISIN 유도(결정적) + 공매도 잔고 파싱 검증."""
from datetime import datetime, timezone

from app.core.errors import ProviderError
from app.data.schema import Kind
from app.providers.krx import KRXProvider, isin_from_code, _parse_short
from app.providers.kis import HttpTransport

NOW = datetime(2026, 6, 19, 8, 0, tzinfo=timezone.utc)

SHORT_OK = {"OutBlock_1": [
    {"TRD_DD": "2026/06/12", "BAL_RTO": "0.40", "BAL_QTY": "1,000", "LIST_SHRS": "250,000"},
    {"TRD_DD": "2026/06/13", "BAL_RTO": "0.55"},
    {"TRD_DD": "2026/06/16", "BAL_RTO": "6.20"},   # 최신, >5% -> 리스크
]}


class FakeTransport(HttpTransport):
    def __init__(self, resp=SHORT_OK):
        self.resp = resp; self.last = None

    def post_form(self, url, headers, data):
        self.last = data
        return 200, self.resp

    def get(self, url, headers, params):
        return 404, {}

    def post(self, url, headers, body):
        return 404, {}


def test_isin_from_code_known():
    # 실제 ISIN 으로 체크디지트 검증
    assert isin_from_code("005930") == "KR7005930003"   # 삼성전자
    assert isin_from_code("000660") == "KR7000660001"   # SK하이닉스
    assert isin_from_code("035420") == "KR7035420009"   # NAVER


def test_parse_short_latest_and_trend():
    ratio, trend, as_of = _parse_short(SHORT_OK["OutBlock_1"], NOW)
    assert ratio == 6.20            # 최신 거래일 잔고비중
    assert trend == "up"            # 0.40 -> 6.20 상승
    assert as_of.year == 2026 and as_of.month == 6 and as_of.day == 16


def test_parse_short_computes_ratio_from_qty():
    rows = [{"TRD_DD": "2026/06/16", "BAL_QTY": "5,000", "LIST_SHRS": "100,000"}]  # 5%
    ratio, _, _ = _parse_short(rows, NOW)
    assert ratio == 5.0


def test_fetch_uses_isin_and_returns_short():
    t = FakeTransport()
    p = KRXProvider(transport=t)
    dp = p.fetch("005930", "short", now=NOW)
    assert t.last["isuCd"] == "KR7005930003"     # ISIN 으로 조회
    assert dp.kind == Kind.SHORT.value
    assert dp.payload["short_balance_ratio"] == 6.20
    assert dp.payload["trend"] == "up"
    assert dp.as_of <= NOW and dp.source == "krx"


def test_empty_raises():
    p = KRXProvider(transport=FakeTransport({"OutBlock_1": []}))
    try:
        p.fetch("005930", "short", now=NOW)
        assert False
    except ProviderError as e:
        assert "공매도" in str(e)


def test_unsupported_kind_returns_none():
    p = KRXProvider(transport=FakeTransport())
    assert p.fetch("005930", "ohlcv", now=NOW) is None
