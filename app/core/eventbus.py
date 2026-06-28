"""EventBus — 모듈 간 결합을 느슨하게 한다.

데이터 갱신/추천 생성/검증 같은 이벤트를 토픽으로 발행하고, 구독자(스케줄러, WS push,
verdict 기록 등)가 직접 호출 의존 없이 반응한다. 핸들러는 락 밖에서 호출해 데드락을 피한다.
"""
from __future__ import annotations

import threading
import traceback
from collections import defaultdict
from typing import Callable, Any

Handler = Callable[[str, Any], None]


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        with self._lock:
            self._subs[topic].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._subs.get(topic, []):
                    self._subs[topic].remove(handler)

        return unsubscribe

    def publish(self, topic: str, payload: Any = None) -> int:
        # 핸들러 스냅샷을 락 안에서 복사하고, 호출은 락 밖에서 한다.
        with self._lock:
            handlers = list(self._subs.get(topic, []))
        delivered = 0
        for h in handlers:
            try:
                h(topic, payload)
                delivered += 1
            except Exception:  # 한 구독자의 실패가 다른 구독자를 막지 않게.
                traceback.print_exc()
        return delivered
