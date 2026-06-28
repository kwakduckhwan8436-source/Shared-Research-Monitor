"""KIS 실시간 파서 테스트 — '^' 구분 파이프 포맷을 fixture 로 검증."""
from datetime import datetime, timezone

from app.data.schema import Kind
from app.providers.kis_ws import parse_message, is_pingpong, TR_TRADE, TR_ORDERBOOK

NOW = datetime(2026, 6, 19, 4, 0, tzinfo=timezone.utc)


def _trade_record(symbol="005930", price="74200", vol="150", strength="123.5"):
    """H0STCNT0 레코드 한 개(필드 인덱스 맞춰 구성)."""
    f = ["0"] * 46
    f[0] = symbol       # 종목코드
    f[1] = "093000"     # 체결시각
    f[2] = price        # 현재가
    f[12] = vol         # 체결량
    f[18] = strength    # 체결강도
    return "^".join(f)


def _orderbook_record(symbol="005930"):
    """H0STASP0 레코드 한 개. 매도/매수 호가·잔량 상위 일부 세팅."""
    f = ["0"] * 60
    f[0] = symbol
    f[1] = "093000"
    # 매도호가1~5 (3..7), 매수호가1~5 (13..17)
    for i in range(5):
        f[3 + i] = str(74300 + i * 100)     # 매도호가
        f[13 + i] = str(74200 - i * 100)    # 매수호가
        f[23 + i] = str(100 + i * 10)       # 매도잔량
        f[33 + i] = str(200 + i * 10)       # 매수잔량
    return "^".join(f)


def test_pingpong_detected_and_no_data():
    raw = '{"header":{"tr_id":"PINGPONG","datetime":"20260619093000"}}'
    assert is_pingpong(raw) is True
    assert parse_message(raw, NOW) == []


def test_subscribe_ack_is_ignored():
    raw = '{"header":{"tr_id":"H0STCNT0"},"body":{"rt_cd":"0","msg1":"SUBSCRIBE SUCCESS"}}'
    assert is_pingpong(raw) is False
    assert parse_message(raw, NOW) == []   # JSON 제어 프레임 -> 데이터 아님


def test_trade_parsed():
    raw = f"0|{TR_TRADE}|001|{_trade_record(price='74200', vol='150', strength='123.5')}"
    dps = parse_message(raw, NOW)
    assert len(dps) == 1
    dp = dps[0]
    assert dp.kind == Kind.TICK.value
    assert dp.symbol == "005930"
    assert dp.payload["price"] == 74200.0
    assert dp.payload["qty"] == 150
    assert dp.payload["strength"] == 123.5
    assert dp.as_of == NOW and dp.source == "kis-ws"


def test_trade_multi_record():
    body = _trade_record(symbol="005930", price="74200") + "^" + \
           _trade_record(symbol="000660", price="180000")
    raw = f"0|{TR_TRADE}|002|{body}"
    dps = parse_message(raw, NOW)
    assert len(dps) == 2
    assert {d.symbol for d in dps} == {"005930", "000660"}
    prices = {d.symbol: d.payload["price"] for d in dps}
    assert prices["000660"] == 180000.0


def test_orderbook_parsed_top5():
    raw = f"0|{TR_ORDERBOOK}|001|{_orderbook_record()}"
    dps = parse_message(raw, NOW)
    assert len(dps) == 1
    ob = dps[0].payload
    assert dps[0].kind == Kind.ORDERBOOK.value
    assert len(ob["asks"]) == 5 and len(ob["bids"]) == 5
    # 매도호가1=74300/잔량100, 매수호가1=74200/잔량200
    assert ob["asks"][0] == [74300.0, 100]
    assert ob["bids"][0] == [74200.0, 200]
    # 잔량 합으로 imbalance 계산 가능한지 (bid>ask로 세팅)
    bid_q = sum(q for _, q in ob["bids"])
    ask_q = sum(q for _, q in ob["asks"])
    assert bid_q > ask_q


def test_unknown_tr_returns_empty():
    raw = "0|H0XXXXX0|001|005930^093000^1^2^3"
    assert parse_message(raw, NOW) == []


def test_malformed_returns_empty():
    assert parse_message("", NOW) == []
    assert parse_message("garbage-without-pipes", NOW) == []
    assert parse_message("0|H0STCNT0", NOW) == []  # body 없음
