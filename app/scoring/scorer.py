"""호라이즌 스코어러.

SignalResult 리스트 -> (raw_score, confidence, 메타).
- raw_score: 발화한 시그널의 가중 호의도 (abstain 은 제외, 0점 취급 금지).
- confidence = coverage * agreement * freshness
    * coverage : 발화 시그널 가중치 합 / 전체 가중치 합
    * agreement: 시그널 값들의 합의도(분산이 낮을수록 1)
    * freshness: 발화 시그널 confidence 평균
- 미캘리브레이션 가중치 사용 시 allow_uncalibrated=True 없으면 NotCalibrated.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from app.core.errors import NotCalibrated
from app.scoring.weights import WeightSet
from app.signals.base import SignalResult


@dataclass(frozen=True)
class ScoredSymbol:
    raw_score: float          # [0,1]
    confidence: float         # [0,1]
    coverage: float
    agreement: float
    freshness: float
    fired: list[SignalResult]
    abstained: list[SignalResult]
    risk_flags: list[str]
    weights_calibrated: bool
    weights_source: str


class HorizonScorer:
    def __init__(self, weightset: WeightSet, *, allow_uncalibrated: bool = False):
        if not weightset.calibrated and not allow_uncalibrated:
            raise NotCalibrated(
                f"horizon={weightset.horizon} 가중치가 미캘리브레이션 "
                f"({weightset.source}). allow_uncalibrated=True 로 명시 opt-in 필요."
            )
        self.ws = weightset

    def score(self, results: list[SignalResult]) -> ScoredSymbol | None:
        fired = [r for r in results if r.fired]
        abstained = [r for r in results if not r.fired]
        if not fired:
            return None  # 발화 시그널 0개 -> 추천 불가(abstain). 절대 0점으로 만들지 않는다.

        total_w = sum(self.ws.weight_of(r.name) for r in results) or 1.0
        fired_w = sum(self.ws.weight_of(r.name) for r in fired)

        num = sum(self.ws.weight_of(r.name) * r.value * r.confidence for r in fired)
        den = sum(self.ws.weight_of(r.name) * r.confidence for r in fired) or 1e-9
        raw_score = num / den

        coverage = fired_w / total_w
        values = [r.value for r in fired]
        dispersion = statistics.pstdev(values) if len(values) > 1 else 0.0
        agreement = max(0.0, 1.0 - 2.0 * dispersion)
        freshness = statistics.mean([r.confidence for r in fired])
        confidence = max(0.0, min(1.0, coverage * agreement * freshness))

        risk_flags: list[str] = []
        for r in fired:
            risk_flags.extend(r.evidence.get("risk_flags", []))

        return ScoredSymbol(
            raw_score=round(raw_score, 4), confidence=round(confidence, 4),
            coverage=round(coverage, 4), agreement=round(agreement, 4),
            freshness=round(freshness, 4), fired=fired, abstained=abstained,
            risk_flags=sorted(set(risk_flags)),
            weights_calibrated=self.ws.calibrated, weights_source=self.ws.source,
        )
