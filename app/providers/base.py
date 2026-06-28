"""DataProvider 추상화.

모든 외부 데이터(KIS/DART/KRX/News)는 이 인터페이스 뒤에 둔다.
계약:
- fetch() 는 데이터 없음/실패/신선도 미달 시 None 을 반환한다. *절대 추정값을 만들지 않는다.*
- 반환 DataPoint 는 as_of/fetched_at 을 정확히 채운다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from app.data.schema import DataPoint


class DataProvider(ABC):
    name: str = "base"
    supported_kinds: tuple[str, ...] = ()

    def supports(self, kind: str) -> bool:
        return kind in self.supported_kinds

    @abstractmethod
    def fetch(self, symbol: str, kind: str, *, now: datetime) -> Optional[DataPoint]:
        ...


class ProviderRouter(DataProvider):
    """kind 별로 적절한 provider 에 라우팅. 시그널은 이 라우터 하나만 본다."""

    name = "router"

    def __init__(self, providers: list[DataProvider]):
        self._providers = providers

    @property
    def supported_kinds(self) -> tuple[str, ...]:  # type: ignore[override]
        out: set[str] = set()
        for p in self._providers:
            out.update(p.supported_kinds)
        return tuple(sorted(out))

    def fetch(self, symbol: str, kind: str, *, now: datetime) -> Optional[DataPoint]:
        for p in self._providers:
            if p.supports(kind):
                return p.fetch(symbol, kind, now=now)
        return None
