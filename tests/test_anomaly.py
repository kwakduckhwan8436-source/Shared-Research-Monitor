"""이상징후 감지 회귀 — 거래량 급증, 수급 z-score, 조용한 매집, 정상=무이상."""
import random
from app.anomaly import detect_anomalies, _zscore, _sev


def _bars(volbase, spike=False, n=30, flat=True, seed=1):
    r = random.Random(seed)
    out = []
    p = 10000.0
    for d in range(n):
        p *= (1.0005 if flat else (1 + r.uniform(-0.01, 0.01)))
        v = volbase * (1 + r.uniform(-0.12, 0.12))
        if spike and d == n - 1:
            v = volbase * 5
        out.append({"t": f"2026-06-{d+1:02d}", "o": p, "h": p * 1.01, "l": p * 0.99, "c": p, "v": v})
    return out


def _supply(n=30, surge=False, accum=False, seed=2):
    r = random.Random(seed)
    out = []
    for d in range(n):
        f = r.uniform(-50000, 50000)
        i = r.uniform(-30000, 30000)
        if accum:
            f, i = 50000.0, 30000.0
        if surge and d == n - 1:
            f = 800000.0
        out.append({"date": f"2026-06-{d+1:02d}", "foreign_net": f, "inst_net": i, "retail_net": -(f + i)})
    return out


def test_zscore_and_severity():
    assert _zscore([100, 100, 100, 100, 100], 100) == 0.0    # 분산 0
    assert _zscore([1, 2, 3], 5) is None                      # 표본 부족
    assert _sev(1.0) == 0.0 and _sev(3.5) == 0.5


def _supply_stable(n=30):
    """추세 없는 정상 수급 — 부호 교대로 연속 매수/매도 streak 없음."""
    out = []
    for d in range(n):
        out.append({"date": f"2026-06-{d+1:02d}", "foreign_net": 1000 * ((-1) ** d),
                    "inst_net": 500 * ((-1) ** d), "retail_net": 0})
    return out


def test_normal_stock_no_anomaly():
    res = detect_anomalies(_bars(1_000_000), _supply_stable())
    assert res["score"] == 0.0 and not res["flags"]


def test_volume_spike_flat_price():
    res = detect_anomalies(_bars(1_000_000, spike=True, flat=True), _supply())
    assert res["score"] > 0
    assert any("거래량" in f["label"] for f in res["flags"])


def test_foreign_surge():
    res = detect_anomalies(_bars(1_000_000), _supply(surge=True))
    assert any("외국인" in f["label"] for f in res["flags"])


def test_quiet_accumulation():
    res = detect_anomalies(_bars(1_000_000, flat=True), _supply(accum=True))
    assert any("매집" in f["label"] for f in res["flags"])


def test_handles_missing_data():
    assert detect_anomalies(None, None)["score"] == 0.0
    assert detect_anomalies(_bars(1_000_000), None)["score"] >= 0.0   # 봉만 있어도 동작
