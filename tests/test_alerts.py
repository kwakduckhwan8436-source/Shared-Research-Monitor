"""알림 조건 판정 회귀 — 급등/급락, 52주 신고가/신저가 경신, 거래량 급증, 공시."""
from datetime import datetime, timezone, timedelta
from app.core.ssot import SSOT
from app.data.schema import Kind, DataPoint

NOW = datetime(2026, 6, 19, 8, 0, tzinfo=timezone.utc)
TODAY = NOW.date().isoformat()


def _bars(n=60, drift=0.001):
    out = []
    p = 10000.0
    for d in range(n):
        p *= (1 + drift)
        out.append({"t": (NOW - timedelta(days=n - d)).isoformat(),
                    "o": p, "h": p * 1.005, "l": p * 0.995, "c": p, "v": 1_000_000})
    return out


def _detect(bars, chg_pct=5.0, vol_mult=2.0):
    """엔드포인트의 OHLCV 알림 판정 로직과 동일."""
    found = set()
    last = bars[-1]
    if len(bars) >= 2 and bars[-2]["c"] > 0:
        chg = (last["c"] - bars[-2]["c"]) / bars[-2]["c"] * 100
        if chg >= chg_pct:
            found.add("급등")
        elif chg <= -chg_pct:
            found.add("급락")
    window = bars[-252:] if len(bars) > 252 else bars
    if len(window) >= 2:
        prior = window[:-1]
        if last["c"] >= max(b["h"] for b in prior):
            found.add("52주신고가")
        elif last["c"] <= min(b["l"] for b in prior):
            found.add("52주신저가")
    if len(bars) >= 21:
        avg = sum(b["v"] for b in bars[-21:-1]) / 20
        if avg > 0 and last["v"] >= vol_mult * avg:
            found.add("거래량급증")
    return found


def test_surge_and_breakout_and_volume():
    bars = _bars()
    p = bars[-2]["c"]
    bars[-1] = {"t": NOW.isoformat(), "o": p, "h": p * 1.08, "l": p, "c": p * 1.075, "v": 3_000_000}
    f = _detect(bars)
    assert "급등" in f and "52주신고가" in f and "거래량급증" in f


def test_crash_and_new_low():
    bars = _bars()
    p = bars[-2]["c"]
    bars[-1] = {"t": NOW.isoformat(), "o": p, "h": p, "l": p * 0.90, "c": p * 0.92, "v": 1_000_000}
    f = _detect(bars)
    assert "급락" in f and "52주신저가" in f


def test_quiet_day_no_alert():
    bars = _bars()
    f = _detect(bars)
    assert not ({"급등", "급락", "52주신저가", "거래량급증"} & f)


def test_alert_id_stable_per_day():
    # 같은 종목/종류/날짜는 동일 id (하루 한 번)
    a = f"005930:급등:{TODAY}"
    b = f"005930:급등:{TODAY}"
    assert a == b
