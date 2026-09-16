from __future__ import annotations

from typing import Any

from .config import RATE_LIMIT_WINDOW_SECONDS


class FixedWindowLimiter:
    def __init__(self, redis: Any, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS) -> None:
        self._redis = redis
        self.window_seconds = window_seconds

    async def hit(self, key: str, limit: int) -> bool:
        if limit <= 0:
            return True
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, self.window_seconds)
        return count <= limit
