"""Signal 베이스.

각 시그널은 데이터의 순수 함수다. 부수효과 없음.
규약:
- value 는 [0,1] 의 '호의도(favorability)'. 1=강한 호재성, 0.5=중립, 0=강한 악재성.
- 필수 데이터가 하나라도 없으면 value=None + abstain_reason (추정금지).
- confidence 는 데이터 신선도·충분도 기반 [0,1].
- evidence 에는 판단 근거가 된 실제 수치를 그대로 담는다(감사·LLM 입력).

베이스 run() 이 DataUnavailable 을 잡아 자동으로 abstain 결과로 변환한다.
서브클래스는 _compute(ctx) 만 구현한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.errors import DataUnavailable
from app.core.ssot import SSOT
from app.data.freshness import require_fresh
from app.data.schema import DataPoint


@dataclass(frozen=True)
class SignalResult:
    name: str
    horizon: str
    value: Optional[float]            # None = abstain
    confidence: float
    evidence: dict = field(default_factory=dict)
    abstain_reason: Optional[str] = None

    @property
    def fired(self) -> bool:
        return self.value is not None


class SignalContext:
    def __init__(self, symbol: str, ssot: SSOT, horizon: str, now: datetime):
        self.symbol = symbol
        self.ssot = ssot
        self.horizon = horizon
        self.now = now

    def require(self, kind: str) -> DataPoint:
        """없거나 신선도 미달이면 DataUnavailable. 통과 시 DataPoint."""
        dp = self.ssot.get(self.symbol, kind)
        return require_fresh(dp, self.symbol, kind, self.horizon, self.now)


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


class Signal(ABC):
    name: str = "signal"
    horizon: str = "swing"
    required_kinds: tuple[str, ...] = ()

    @abstractmethod
    def _compute(self, ctx: SignalContext) -> SignalResult:
        ...

    def run(self, ctx: SignalContext) -> SignalResult:
        try:
            return self._compute(ctx)
        except DataUnavailable as e:
            return SignalResult(self.name, self.horizon, value=None,
                                confidence=0.0, abstain_reason=str(e))

    def abstain(self, reason: str) -> SignalResult:
        return SignalResult(self.name, self.horizon, value=None, confidence=0.0,
                            abstain_reason=reason)
