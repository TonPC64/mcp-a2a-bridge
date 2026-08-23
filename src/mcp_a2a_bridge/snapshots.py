"""Thread-safe, coalescing snapshot subscriptions for SSE streams."""

from __future__ import annotations

import queue
import threading
from typing import Any


class SnapshotSubscribers:
    """Fan out the newest complete snapshot without blocking publishers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def publish(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(snapshot)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber.put_nowait(snapshot)
                except queue.Full:
                    pass

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._subscribers)
