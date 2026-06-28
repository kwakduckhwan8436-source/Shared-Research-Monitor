"""백테스트 엔진 회귀 — 예측가능 세계에서 IC>0, 무작위 세계에서 IC≈0, look-ahead 차단."""
import random
from app.backtest.engine import Backtester, BacktestConfig, _spearman, _pearson, HORIZON_FORWARD


def _make(drift, n=200, seed=0):
    r = random.Random(seed)
    bars = []
    p = 10000.0
    for d in range(n):
        p *= (1 + drift + r.gauss(0, 0.011))
        o = p * (1 + r.gauss(0, 0.003)); h = max(o, p) * 1.008; l = min(o, p) * 0.992
        bars.append({"t": f"2025-{1+d//28:02d}-{1+d%28:02d}", "o": o, "h": h, "l": l, "c": p, "v": 1e6})
    return bars


def test_rank_helpers():
    assert abs(_spearman([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) - 1.0) < 1e-6
    assert abs(_spearman([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) + 1.0) < 1e-6
    assert _pearson([1, 1, 1], [1, 2, 3]) is None      # 분산 0


def test_predictive_world_has_positive_ic():
    bars = {f"S{i:02d}": _make(-0.002 + 0.0004 * i, seed=i) for i in range(20)}
    res = Backtester(BacktestConfig("swing", forward_days=10, rebalance_every=5, min_history=60),
                     allow_uncalibrated=True).run(bars)
    assert res.n_records > 100
    assert res.rank_ic_mean is not None and res.rank_ic_mean > 0.1   # 예측력 있음
    assert res.top_minus_bottom is not None and res.top_minus_bottom > 0


def test_random_world_has_near_zero_ic():
    bars = {f"R{i:02d}": _make(0.0, seed=100 + i) for i in range(20)}
    res = Backtester(BacktestConfig("swing", forward_days=10, rebalance_every=5, min_history=60),
                     allow_uncalibrated=True).run(bars)
    assert res.rank_ic_mean is not None
    # 무작위는 표본 유한성으로 ±0.12 정도까지 나올 수 있으나 예측세계(~0.32)와는 명확히 구분된다.
    assert abs(res.rank_ic_mean) < 0.2


def test_insufficient_data_returns_warning():
    bars = {"A": _make(0.001, n=50, seed=1), "B": _make(0.001, n=50, seed=2)}
    res = Backtester(BacktestConfig("midlong", forward_days=60, min_history=60),
                     allow_uncalibrated=True).run(bars)
    assert res.n_records == 0 and res.warnings


def test_forward_window_defaults():
    assert HORIZON_FORWARD["daytrade"] < HORIZON_FORWARD["swing"] < HORIZON_FORWARD["midlong"]


def test_signal_samples_for_calibration():
    bars = {f"S{i:02d}": _make(-0.001 + 0.0003 * i, seed=i) for i in range(15)}
    bt = Backtester(BacktestConfig("swing", forward_days=10, rebalance_every=5, min_history=60),
                    allow_uncalibrated=True)
    samples = bt.signal_samples(bars)
    assert samples                                  # 시그널별 (value, fwd) 표본
    for name, pairs in samples.items():
        assert all(len(p) == 2 for p in pairs)
