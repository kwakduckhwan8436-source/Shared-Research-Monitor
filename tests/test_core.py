"""core 계층 테스트."""
from datetime import datetime, timezone, timedelta

from app.core.clock import FrozenClock, to_kst, KST
from app.core.eventbus import EventBus
from app.core.ssot import SSOT
from app.data.schema import DataPoint, Kind


def _dp(symbol="005930", kind=Kind.OHLCV.value, payload=None, now=None):
    now = now or datetime(2026, 6, 19, tzinfo=timezone.utc)
    return DataPoint(symbol, kind, payload or {"bars": []}, as_of=now, fetched_at=now, source="t")


def test_frozen_clock_advances():
    c = FrozenClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    t0 = c.now()
    c.advance(days=2)
    assert (c.now() - t0) == timedelta(days=2)


def test_to_kst_offset():
    t = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert to_kst(t).hour == 9  # UTC+9


def test_eventbus_pub_sub_and_unsubscribe():
    bus = EventBus()
    got = []
    unsub = bus.subscribe("x", lambda t, p: got.append(p))
    assert bus.publish("x", 1) == 1
    assert got == [1]
    unsub()
    assert bus.publish("x", 2) == 0
    assert got == [1]


def test_eventbus_isolates_handler_errors():
    bus = EventBus()
    got = []
    bus.subscribe("x", lambda t, p: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe("x", lambda t, p: got.append(p))
    bus.publish("x", 9)
    assert got == [9]  # 한 핸들러 실패가 다른 핸들러를 막지 않음


def test_ssot_snapshot_deterministic_and_changes():
    a, b = SSOT(), SSOT()
    a.put(_dp(payload={"bars": [1, 2]}))
    b.put(_dp(payload={"bars": [1, 2]}))
    assert a.snapshot_id() == b.snapshot_id()       # 같은 내용 -> 같은 지문
    b.put(_dp(payload={"bars": [1, 2, 3]}))
    assert a.snapshot_id() != b.snapshot_id()       # 내용 바뀌면 지문 변경
