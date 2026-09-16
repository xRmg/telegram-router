from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import ValidationError

from .config import Config
from .keys import OUTGOING_CHANNEL, notifier_rate_limit_key
from .models import OutgoingMessage
from .notifier import Notifier
from .ratelimit import FixedWindowLimiter
from .registry import CapabilityRegistry, resolve_display_name
from .router import Router

_LEVEL_ORDER = {"info": 0, "warning": 1, "critical": 2}


class OutgoingRelay:
    def __init__(
        self,
        redis: Any,
        notifier: Notifier,
        registry: CapabilityRegistry,
        config: Config,
        router: Router,
        limiter: FixedWindowLimiter,
        logger: logging.Logger | None = None,
    ) -> None:
        self._redis = redis
        self._notifier = notifier
        self._registry = registry
        self._config = config
        self._router = router
        self._limiter = limiter
        self._logger = logger or logging.getLogger(__name__)
        self._overflow: dict[str, tuple[int, str]] = {}
        self._summary_tasks: dict[str, asyncio.Task[None]] = {}

    async def run(self) -> None:
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(OUTGOING_CHANNEL)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    await self.handle(str(message["data"]))
                except Exception:
                    self._logger.exception("outgoing_handle_failed")
        finally:
            await pubsub.aclose()

    async def handle(self, raw: str) -> None:
        try:
            message = OutgoingMessage.model_validate_json(raw)
        except ValidationError as exc:
            self._logger.warning("outgoing_rejected", extra={"error": str(exc)})
            return
        name = resolve_display_name(message.service_id, self._config, self._registry)
        text = f"{name}: {message.text}"
        if message.request_id is not None:
            resolved = await self._router.resolve_reply(message)
            if not resolved:
                self._logger.warning(
                    "outgoing_orphan_reply",
                    extra={"service_id": message.service_id, "request_id": message.request_id},
                )
                return
            await self._notifier.send(text, level=message.level)
            return
        key = notifier_rate_limit_key(message.service_id)
        if await self._limiter.hit(key, self._config.notifier_rate_limit_per_minute):
            await self._notifier.send(text, level=message.level)
            self._logger.info(
                "push_relayed", extra={"service_id": message.service_id, "level": message.level}
            )
            return
        self._record_overflow(message.service_id, message.level)
        self._logger.info(
            "push_suppressed",
            extra={"service_id": message.service_id, "level": message.level},
        )

    def _record_overflow(self, service_id: str, level: str) -> None:
        count, current = self._overflow.get(service_id, (0, "info"))
        self._overflow[service_id] = (count + 1, _higher_level(current, level))
        if service_id not in self._summary_tasks:
            self._summary_tasks[service_id] = asyncio.create_task(
                self._summary_after_window(service_id)
            )

    async def _summary_after_window(self, service_id: str) -> None:
        try:
            ttl = await self._redis.ttl(notifier_rate_limit_key(service_id))
            delay = ttl if ttl and ttl > 0 else self._limiter.window_seconds
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self._summary_tasks.pop(service_id, None)
            raise
        count, level = self._overflow.pop(service_id, (0, "info"))
        self._summary_tasks.pop(service_id, None)
        if count <= 0:
            return
        name = resolve_display_name(service_id, self._config, self._registry)
        await self._notifier.send(
            f"{name}: {count} more notification(s) suppressed by the rate limit", level=level
        )
        self._logger.info(
            "push_summary_sent", extra={"service_id": service_id, "suppressed": count}
        )


def _higher_level(left: str, right: str) -> str:
    return left if _LEVEL_ORDER[left] >= _LEVEL_ORDER[right] else right
