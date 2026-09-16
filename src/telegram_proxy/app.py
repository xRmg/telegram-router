from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot
from redis.asyncio import Redis

from .bot import ProxyBot, TelegramNotifier
from .config import Config, ConfigError
from .llm import ToolRouter
from .logging_setup import setup_logging
from .outgoing import OutgoingRelay
from .ratelimit import FixedWindowLimiter
from .registry import CapabilityRegistry
from .router import Router


async def run(config: Config) -> None:
    logger = logging.getLogger("telegram_proxy")
    redis = Redis.from_url(config.redis_url, decode_responses=True)
    bot = Bot(config.telegram_bot_token)
    registry = CapabilityRegistry(redis, db_index=config.redis_db_index, logger=logger)
    notifier = TelegramNotifier(bot, config.telegram_owner_chat_id, logger=logger)
    limiter = FixedWindowLimiter(redis)
    llm = ToolRouter(config, registry, logger=logger)
    router = Router(redis, registry, config, notifier, llm, limiter, logger=logger)
    relay = OutgoingRelay(redis, notifier, registry, config, router, limiter, logger=logger)
    proxy_bot = ProxyBot(bot, config, router, registry, notifier, redis, logger=logger)

    await registry.start()
    tasks = [
        asyncio.create_task(relay.run(), name="outgoing-relay"),
        asyncio.create_task(proxy_bot.run(), name="telegram-poller"),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await registry.stop()
        await bot.session.close()
        await redis.aclose()


def main() -> None:
    setup_logging()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        pass
