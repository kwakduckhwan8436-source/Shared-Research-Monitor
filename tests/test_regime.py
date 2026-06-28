"""시장 국면 판정 회귀 — 강세/약세/고변동/횡보/데이터부족."""
import random
from app.regime import detect_regime, _annualized_vol, _vix_state


def _series(start, drift, n=260, vol=0.004, seed=1):
    r = random.Random(seed)
    out = [start]
    for _ in range(n - 1):
        out.append(out[-1] * (1 + drift + r.gauss(0, vol)))
    return out


def test_bull_uptrend_low_vix():
    reg = detect_regime(_series(2000, 0.0008), vix=13)
    assert reg.regime == "bull"
    assert reg.vs_ma_pct > 0 and reg.confidence > 0.5


def test_bear_downtrend():
    reg = detect_regime(_series(3000, -0.0010), vix=22)
    assert reg.regime == "bear"
    assert reg.ret_60d < 0


def test_high_vix_forces_bear():
    # 추세가 완만해도 VIX 공포면 약세 처리
    reg = detect_regime(_series(2500, 0.0001), vix=35)
    assert reg.regime == "bear"
    assert reg.vix_state == "공포"


def test_range_when_flat():
    # 드리프트 0 + 저변동 → 추세 없음(횡보)
    reg = detect_regime(_series(2600, 0.0, n=260, vol=0.0015, seed=42), vix=16)
    assert reg.regime == "range"


def test_insufficient_data():
    reg = detect_regime([2600, 2610, 2620], vix=None)
    assert reg.confidence == 0.0


def test_vix_state_thresholds():
    assert _vix_state(12) == "안정" and _vix_state(18) == "보통"
    assert _vix_state(24) == "불안" and _vix_state(32) == "공포"


def test_guidance_has_all_horizons():
    reg = detect_regime(_series(2000, 0.0008), vix=13)
    for k in ("summary", "daytrade", "swing", "midlong", "caution"):
        assert k in reg.guidance and reg.guidance[k]


def test_realized_vol_positive():
    v = _annualized_vol(_series(2000, 0.0, vol=0.01, seed=5), 20)
    assert v is not None and v > 0
