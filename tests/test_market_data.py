"""해외지수 provider 파싱 회귀 테스트 (야후 chart JSON / stooq CSV)."""
from app.providers.market_data import MarketDataProvider


def test_yahoo_parse_ok():
    p = MarketDataProvider()
    txt = ('{"chart":{"result":[{"meta":{"regularMarketPrice":17850.5,'
           '"chartPreviousClose":17710.2,"previousClose":17710.2}}],"error":null}}')
    r = p.parse_yahoo(txt)
    assert r is not None and r["value"] == 17850.5
    assert abs(r["change_pct"] - 0.79) < 0.05
    assert r["source"] == "yahoo"


def test_yahoo_parse_uses_previousClose_fallback():
    p = MarketDataProvider()
    txt = ('{"chart":{"result":[{"meta":{"regularMarketPrice":100.0,'
           '"previousClose":80.0}}]}}')
    r = p.parse_yahoo(txt)
    assert r is not None and abs(r["change_pct"] - 25.0) < 0.01


def test_yahoo_parse_garbage_none():
    p = MarketDataProvider()
    assert p.parse_yahoo("not json") is None
    assert p.parse_yahoo('{"chart":{"result":[]}}') is None


def test_stooq_parse_ok():
    p = MarketDataProvider()
    csv = ("Symbol,Date,Time,Open,High,Low,Close,Volume\n"
           "^SPX,2026-06-20,22:00:00,5400,5450,5390,5432.10,0")
    r = p.parse_stooq(csv)
    assert r is not None and r["value"] == 5432.10
    assert r["change_pct"] is None   # stooq 폴백은 등락 미제공
    assert r["source"] == "stooq"


def test_stooq_parse_no_data_none():
    p = MarketDataProvider()
    assert p.parse_stooq("Symbol,Close\n") is None
    assert p.parse_stooq("Symbol,Date,Close\n^X,2026,N/D") is None
