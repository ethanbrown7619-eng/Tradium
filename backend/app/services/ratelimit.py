"""
Async sliding-window rate limiter to keep Polymarket API usage under limits.

Clock and sleep are injectable so the accounting is deterministically testable
without real time.
"""
import asyncio
import time
from collections import deque
from typing import Callable, Optional


class AsyncRateLimiter:
    def __init__(self, max_calls: int, period: float,
                 clock: Optional[Callable[[], float]] = None,
                 sleep: Optional[Callable] = None):
        self.max_calls = max_calls
        self.period = period
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._calls: deque = deque()
        self._lock = asyncio.Lock()

    def _evict(self, now: float):
        while self._calls and now - self._calls[0] >= self.period:
            self._calls.popleft()

    async def acquire(self):
        async with self._lock:
            now = self._clock()
            self._evict(now)
            if len(self._calls) >= self.max_calls:
                wait = self.period - (now - self._calls[0])
                if wait > 0:
                    await self._sleep(wait)
                now = self._clock()
                self._evict(now)
            self._calls.append(self._clock())
