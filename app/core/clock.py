"""Clock — 모든 '현재 시각'은 여기를 통해 주입한다.

이유:
- lookahead bias 차단: 시그널은 as_of <= clock.now() 데이터만 본다.
- 재현성/테스트: FrozenClock 으로 시간을 고정하면 멱등 테스트가 가능하다.

내부 표준은 UTC. 한국 장 시각 표시는 edge(표현 계층)에서 KST로 변환한다.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


class Clock:
    """실시간 시계."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock(Clock):
    """고정 시계 — 테스트·백테스트·재현용."""

    def __init__(self, t: datetime):
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        self._t = t

    def now(self) -> datetime:
        return self._t

    def set(self, t: datetime) -> None:
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        self._t = t

    def advance(self, **kwargs) -> None:
        self._t = self._t + timedelta(**kwargs)


def to_kst(t: datetime) -> datetime:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(KST)


def is_market_hours(now: datetime) -> bool:
    """한국 정규장 시간(평일 09:00~15:30 KST)인지. 공휴일은 별도 처리(호출측).
    장중이면 거래대금/거래량이 누적 변동하므로 주기적 갱신이 필요하다."""
    k = to_kst(now)
    if k.weekday() >= 5:          # 토(5)·일(6)
        return False
    hm = k.hour * 60 + k.minute
    return (9 * 60) <= hm <= (15 * 60 + 30)
