"""호라이즌 보강 신호 회귀 테스트 — 일봉만으로 동작 + 과열가드 억제."""
from datetime import datetime, timezone, timedelta
from app.signals.base import SignalContext
from app.signals.common import OverheatGuard, _rsi
from app.signals.daytrade import IntradayStrength, OpeningGap
from app.signals.midlong import LongTermTrend
from app.core.ssot import SSOT
from app.data.schema import Kind, DataPoint

NOW = datetime(2026, 6, 19, 8, 0, tzinfo=timezone.utc)


def _ssot_with_bars(bars):
    s = SSOT()
    s.put(DataPoint("X", Kind.OHLCV.value, {"bars": bars}, NOW, NOW, "t"))
    return s


def _ramp(n, step):
    bars = []
    p = 10000.0
    for d in range(n):
        p *= (1 + step)
        bars.append({"t": (NOW - timedelta(days=n - d)).isoformat(),
                     "o": p * 0.999, "h": p * 1.005, "l": p * 0.995, "c": p, "v": 1_000_000})
    return bars


def test_rsi_extremes():
    up = [10000 * (1.02 ** i) for i in range(30)]
    assert _rsi(up, 14) > 95            # 계속 오르면 RSI 높음
    assert _rsi([100, 101], 14) is None  # 데이터 부족


def test_overheat_guard_penalizes_overbought():
    s = _ssot_with_bars(_ramp(40, 0.02))       # 급등 → 과매수
    r = OverheatGuard("swing").run(SignalContext("X", s, "swing", NOW))
    assert r.fired and r.value < 0.2            # 과매수면 호의도 낮음


def test_overheat_guard_neutral_calm():
    # 등락이 섞인 횡보(상승/하락 번갈아) → RSI 중립권
    bars = []
    p = 10000.0
    for d in range(40):
        p *= (1.01 if d % 2 == 0 else 0.995)        # 번갈아 등락
        bars.append({"t": (NOW - timedelta(days=40 - d)).isoformat(),
                     "o": p, "h": p * 1.005, "l": p * 0.995, "c": p, "v": 1_000_000})
    s = _ssot_with_bars(bars)
    r = OverheatGuard("swing").run(SignalContext("X", s, "swing", NOW))
    assert r.fired and r.value > 0.5            # 과매수 아니면 호의도 높음(중립)


def test_daytrade_signals_from_ohlcv():
    bars = _ramp(30, 0.005)
    bars[-1] = {**bars[-1], "l": bars[-1]["o"] * 0.98, "h": bars[-1]["c"] * 1.001}
    s = _ssot_with_bars(bars)
    r1 = IntradayStrength().run(SignalContext("X", s, "daytrade", NOW))
    r2 = OpeningGap().run(SignalContext("X", s, "daytrade", NOW))
    assert r1.fired and 0.0 <= r1.value <= 1.0   # 호가 없이 일봉으로 발화
    assert r2.fired and 0.0 <= r2.value <= 1.0


def test_midlong_trend_from_ohlcv():
    s = _ssot_with_bars(_ramp(140, 0.003))       # 장기 우상향
    r = LongTermTrend().run(SignalContext("X", s, "midlong", NOW))
    assert r.fired and r.value > 0.6             # 재무 없이도 발화, 상승추세 높음
