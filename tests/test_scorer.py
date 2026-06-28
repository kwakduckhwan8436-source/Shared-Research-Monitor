"""스코어러 테스트 — 미캘리브레이션 가드 + abstain 제외 + 신뢰도."""
from app.core.errors import NotCalibrated
from app.scoring.scorer import HorizonScorer
from app.scoring.weights import WeightSet, default_weights
from app.signals.base import SignalResult


def _r(name, value, conf=0.9, horizon="swing"):
    return SignalResult(name, horizon, value=value, confidence=conf,
                        evidence={} if value is not None else {},
                        abstain_reason=None if value is not None else "no data")


def test_uncalibrated_weights_blocked_without_optin():
    ws = default_weights("swing")  # calibrated=False
    try:
        HorizonScorer(ws)  # opt-in 안 함
        assert False, "should raise NotCalibrated"
    except NotCalibrated:
        pass


def test_uncalibrated_allowed_with_optin_and_labeled():
    ws = default_weights("swing")
    scorer = HorizonScorer(ws, allow_uncalibrated=True)
    res = scorer.score([_r("ma_alignment", 1.0), _r("volume_breakout", 0.5)])
    assert res is not None
    assert res.weights_calibrated is False  # 결과에 미캘리브 라벨이 따라붙음


def test_all_abstain_returns_none():
    scorer = HorizonScorer(default_weights("swing"), allow_uncalibrated=True)
    res = scorer.score([_r("ma_alignment", None), _r("volume_breakout", None)])
    assert res is None  # 전부 abstain -> 추천 불가(0점 아님)


def test_abstain_excluded_from_coverage():
    scorer = HorizonScorer(default_weights("swing"), allow_uncalibrated=True)
    # ma_alignment(0.25) 발화, 나머지 abstain
    res = scorer.score([
        _r("ma_alignment", 1.0),
        _r("foreign_inst_streak", None),
        _r("volume_breakout", None),
        _r("news_sentiment", None),
        _r("risk_flags", None),
    ])
    assert res is not None
    assert res.coverage < 0.5     # 일부만 발화 -> 커버리지 낮음
    assert len(res.fired) == 1
    assert len(res.abstained) == 4


def test_calibrated_weights_pass_without_optin():
    ws = WeightSet("swing", {"ma_alignment": 1.0}, calibrated=True, source="test")
    scorer = HorizonScorer(ws)  # opt-in 불필요
    res = scorer.score([_r("ma_alignment", 0.8)])
    assert res is not None and res.weights_calibrated is True
