"""In-memory sliding-window rate limiter.

Не требует внешних зависимостей (Redis etc).
Достаточно для single-instance MVP.

Zero Trust: каждый IP + tier имеет собственное окно.
cleanup_stale вызывается из GameGC для предотвращения утечки памяти.
"""

import time
from collections import defaultdict
from threading import Lock
from typing import Dict, List


class RateLimiter:
    """Скользящее окно: N запросов за M секунд на ключ (например, IP/tier)."""

    def __init__(self, max_requests: int = 20, window_seconds: int = 1):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._windows: Dict[str, List[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, key: str) -> bool:
        """True — запрос разрешён, False — rate limited (429)."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            window = self._windows[key]
            while window and window[0] < cutoff:
                window.pop(0)

            if len(window) >= self.max_requests:
                return False

            window.append(now)
            return True

    def cleanup_stale(self, max_age: float = 3600.0):
        """Удаляет ключи, к которым не обращались дольше max_age секунд.

        Вызывается из GameGC каждые CLEANUP_INTERVAL секунд.
        """
        now = time.monotonic()
        cutoff = now - max_age
        with self._lock:
            for key in list(self._windows.keys()):
                ts_list = self._windows[key]
                if not ts_list or ts_list[-1] < cutoff:
                    del self._windows[key]

    def current_count(self, key: str) -> int:
        """Количество запросов в текущем окне (для метрик/логов)."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            window = self._windows.get(key, [])
            while window and window[0] < cutoff:
                window.pop(0)
            return len(window)

    def total_keys(self) -> int:
        with self._lock:
            return len(self._windows)