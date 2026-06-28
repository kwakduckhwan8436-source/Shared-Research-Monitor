"""호라이즌별 시그널 집합.

혼합 운용의 척추: 단타/스윙/중장기는 각자의 시그널 집합을 갖는다.
공통 시그널(뉴스 감성·리스크)은 각 호라이즌에 해당 호라이즌 라벨로 주입한다.
"""
from __future__ import annotations

from app.signals.base import Signal
from app.signals.swing import SWING_SIGNALS
from app.signals.daytrade import DAYTRADE_SIGNALS
from app.signals.midlong import MIDLONG_SIGNALS
from app.signals.common import NewsSentiment, RiskFlags, PriceMomentum, OverheatGuard

HORIZONS = ("daytrade", "swing", "midlong")


def signals_for(horizon: str) -> list[Signal]:
    base: list[type[Signal]] = []
    if horizon == "daytrade":
        base = list(DAYTRADE_SIGNALS)
    elif horizon == "swing":
        base = list(SWING_SIGNALS)
    elif horizon == "midlong":
        base = list(MIDLONG_SIGNALS)
    else:
        raise ValueError(f"unknown horizon: {horizon}")
    instances: list[Signal] = [cls() for cls in base]
    # 공통 시그널은 해당 호라이즌 라벨로
    instances.append(NewsSentiment(horizon=horizon))
    instances.append(RiskFlags(horizon=horizon))
    instances.append(PriceMomentum(horizon=horizon))   # 일봉 기반 — 어느 호라이즌이든 발화
    instances.append(OverheatGuard(horizon=horizon))   # 과열(RSI) 가드 — 모멘텀 추격 억제
    return instances


def required_kinds_for(horizon: str) -> tuple[str, ...]:
    kinds: set[str] = set()
    for s in signals_for(horizon):
        kinds.update(s.required_kinds)
    return tuple(sorted(kinds))
