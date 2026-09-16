import asyncio

from telegram_proxy.ratelimit import FixedWindowLimiter


async def test_allows_up_to_limit(redis):
    limiter = FixedWindowLimiter(redis, window_seconds=60)
    assert await limiter.hit("key", 2) is True
    assert await limiter.hit("key", 2) is True
    assert await limiter.hit("key", 2) is False


async def test_counters_are_keyed(redis):
    limiter = FixedWindowLimiter(redis, window_seconds=60)
    assert await limiter.hit("a", 1) is True
    assert await limiter.hit("a", 1) is False
    assert await limiter.hit("b", 1) is True


async def test_non_positive_limit_is_unlimited(redis):
    limiter = FixedWindowLimiter(redis, window_seconds=60)
    assert await limiter.hit("key", 0) is True
    assert await limiter.hit("key", -1) is True


async def test_window_expiry_resets(redis):
    limiter = FixedWindowLimiter(redis, window_seconds=1)
    assert await limiter.hit("key", 1) is True
    assert await limiter.hit("key", 1) is False
    await asyncio.sleep(1.1)
    assert await limiter.hit("key", 1) is True
