"""시그널 테스트 — 데이터 없으면 abstain, 있으면 계산."""
from datetime import datetime, timezone

from app.core.ssot import SSOT
from app.providers.mock import MockProvider
from app.signals.base import SignalContext
from app.signals.swing import MovingAverageAlignment, ForeignInstNetBuyStreak
from app.signals.common import RiskFlags
from app.data.schema import Kind

NOW = datetime(2026, 6, 19, 7, 0, tzinfo=timezone.utc)


def _ssot_with(symbol, kinds):
    ssot = SSOT()
    mp = MockProvider()
    for k in kinds:
        dp = mp.fetch(symbol, k, now=NOW)
        if dp:
            ssot.put(dp)
    return ssot


def test_ma_alignment_abstains_without_ohlcv():
    ssot = _ssot_with("005930", [])  # 데이터 없음
    ctx = SignalContext("005930", ssot, "swing", NOW)
    r = MovingAverageAlignment().run(ctx)
    assert not r.fired
    assert r.value is None
    assert r.abstain_reason


def test_ma_alignment_fires_with_ohlcv():
    ssot = _ssot_with("005930", [Kind.OHLCV.value])
    ctx = SignalContext("005930", ssot, "swing", NOW)
    r = MovingAverageAlignment().run(ctx)
    assert r.fired
    assert 0.0 <= r.value <= 1.0
    assert "ma5" in r.evidence


def test_foreign_streak_detects_scenario():
    # 005930 은 외인 연속 순매수 시나리오 심볼
    ssot = _ssot_with("005930", [Kind.SUPPLY.value])
    ctx = SignalContext("005930", ssot, "swing", NOW)
    r = ForeignInstNetBuyStreak().run(ctx)
    assert r.fired
    assert r.evidence["streak_days"] >= 1


def test_risk_flags_surface_disclosure_risk():
    # 068270 셀트리온은 유증 뉴스 -> 리스크 플래그
    ssot = _ssot_with("068270", [Kind.NEWS.value, Kind.SHORT.value])
    ctx = SignalContext("068270", ssot, "swing", NOW)
    r = RiskFlags(horizon="swing").run(ctx)
    assert r.fired
    assert any("유증" in f for f in r.evidence["risk_flags"])
    assert r.value < 1.0  # 리스크가 호의도를 낮춤
