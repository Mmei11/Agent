"""通用熔断器。

工具层与会话层共用：连续失败到阈值后短暂摘除，避免每一轮都白等一次超时。
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class CircuitBreaker:
    def __init__(
        self,
        threshold: int = 3,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._failures = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            return self._clock() >= self._open_until

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold:
                self._open_until = self._clock() + self.cooldown_s
                self._failures = 0
