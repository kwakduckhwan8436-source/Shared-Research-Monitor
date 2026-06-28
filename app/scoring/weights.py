"""가중치 세트.

핵심: 가중치가 '통계적으로 캘리브레이션된 것'인지 '규칙기반 기본값'인지 라벨을 단다.
규칙기반 기본값을 쓰려면 호출자가 allow_uncalibrated=True 로 의식적으로 opt-in 해야 하고,
모든 추천 결과에 weights_calibrated=False 가 따라붙는다 (조용한 기본값 사용 금지).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WeightSet:
    horizon: str
    weights: dict[str, float]
    calibrated: bool
    source: str

    def weight_of(self, signal_name: str) -> float:
        return self.weights.get(signal_name, 0.0)


# 규칙기반 기본 가중치 v1 — 해석가능·단순. *통계적 캘리브레이션 아님.*
# verdict 데이터가 쌓이면 scoring.calibration 이 calibrated=True 세트로 대체.
RULE_BASED: dict[str, WeightSet] = {
    "daytrade": WeightSet("daytrade", {
        "volume_surge": 0.22, "orderbook_imbalance": 0.18,
        "intraday_strength": 0.15, "opening_gap": 0.10, "price_momentum": 0.10,
        "overheat_guard": 0.10, "news_sentiment": 0.08, "risk_flags": 0.07,
    }, calibrated=False, source="rule-based default v1"),
    "swing": WeightSet("swing", {
        "ma_alignment": 0.20, "foreign_inst_streak": 0.18, "volume_breakout": 0.15,
        "price_momentum": 0.12, "overheat_guard": 0.10,
        "news_sentiment": 0.12, "risk_flags": 0.13,
    }, calibrated=False, source="rule-based default v1"),
    "midlong": WeightSet("midlong", {
        "valuation_percentile": 0.24, "earnings_growth": 0.24, "long_term_trend": 0.16,
        "price_momentum": 0.08, "overheat_guard": 0.06,
        "news_sentiment": 0.10, "risk_flags": 0.12,
    }, calibrated=False, source="rule-based default v1"),
}


def default_weights(horizon: str) -> WeightSet:
    if horizon not in RULE_BASED:
        raise ValueError(f"no default weights for horizon: {horizon}")
    return RULE_BASED[horizon]
