"""SSOT (Single Source of Truth) — 정규화 데이터의 단일 소유자.

(symbol, kind) -> 최신 DataPoint. 모든 쓰기는 RLock 으로 직렬화한다.
시그널/스코어러는 여기서만 데이터를 읽는다 (provider 구현을 모른다).

snapshot_id(): 현재 상태의 결정적 해시. 같은 스냅샷이면 같은 추천이 재현되어야 한다(멱등성).
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from typing import Optional

from app.data.schema import DataPoint


def _payload_digest(payload) -> str:
    try:
        s = json.dumps(payload, sort_keys=True, default=str)
    except TypeError:
        s = repr(payload)
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


class SSOT:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[tuple[str, str], DataPoint] = {}

    def put(self, dp: DataPoint) -> None:
        with self._lock:
            self._data[(dp.symbol, dp.kind)] = dp

    def get(self, symbol: str, kind: str) -> Optional[DataPoint]:
        with self._lock:
            return self._data.get((symbol, kind))

    def symbols(self) -> list[str]:
        with self._lock:
            return sorted({k[0] for k in self._data})

    def snapshot_id(self) -> str:
        """현재 보유 데이터 전체에 대한 결정적 지문."""
        with self._lock:
            parts = []
            for (sym, kind) in sorted(self._data):
                dp = self._data[(sym, kind)]
                parts.append(f"{sym}|{kind}|{dp.as_of.isoformat()}|{_payload_digest(dp.payload)}")
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]

    def size(self) -> int:
        with self._lock:
            return len(self._data)
