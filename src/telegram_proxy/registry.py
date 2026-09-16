from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import ValidationError

from .config import Config
from .keys import (
    CAPABILITIES_CHANGED_CHANNEL,
    CAPABILITIES_KEY_PREFIX,
    capabilities_key,
    keyspace_expired_channel,
)
from .models import Capability


class CapabilityRegistry:
    def __init__(
        self,
        redis: Any,
        db_index: int = 0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._redis = redis
        self._db_index = db_index
        self._logger = logger or logging.getLogger(__name__)
        self._capabilities: dict[str, Capability] = {}
        self._prefix_index: dict[str, str] = {}
        self._listener: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.refresh()
        self._listener = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._listener is None:
            return
        self._listener.cancel()
        try:
            await self._listener
        except asyncio.CancelledError:
            pass
        self._listener = None

    def all(self) -> list[Capability]:
        return list(self._capabilities.values())

    def get(self, service_id: str) -> Capability | None:
        return self._capabilities.get(service_id)

    def by_prefix(self, prefix: str) -> Capability | None:
        service_id = self._prefix_index.get(prefix)
        if service_id is None:
            return None
        return self._capabilities.get(service_id)

    def by_ref(self, ref: str) -> Capability | None:
        capability = self.by_prefix(ref.casefold())
        if capability is not None:
            return capability
        capability = self._capabilities.get(ref)
        if capability is not None:
            return capability
        lowered = ref.casefold()
        for candidate in self._capabilities.values():
            if candidate.display_name and candidate.display_name.casefold() == lowered:
                return candidate
        return None

    def has_routable_commands(self) -> bool:
        return any(capability.commands for capability in self._capabilities.values())

    async def is_live(self, service_id: str) -> bool:
        return bool(await self._redis.exists(capabilities_key(service_id)))

    async def refresh(self) -> None:
        async for key in self._redis.scan_iter(match=f"{CAPABILITIES_KEY_PREFIX}*"):
            raw = await self._redis.get(key)
            if raw is not None:
                self._apply(str(key)[len(CAPABILITIES_KEY_PREFIX) :], str(raw))

    async def refresh_service(self, service_id: str) -> None:
        raw = await self._redis.get(capabilities_key(service_id))
        if raw is None:
            self.drop_service(service_id)
            return
        self._apply(service_id, str(raw))

    def drop_service(self, service_id: str) -> None:
        if self._capabilities.pop(service_id, None) is not None:
            self._rebuild_prefix_index()

    def _apply(self, service_id: str, raw: str) -> None:
        try:
            capability = Capability.model_validate_json(raw)
        except ValidationError as exc:
            self._logger.warning(
                "capability_rejected",
                extra={"service_id": service_id, "error": str(exc)},
            )
            return
        if capability.service_id != service_id:
            self._logger.warning(
                "capability_service_id_mismatch",
                extra={
                    "key_service_id": service_id,
                    "payload_service_id": capability.service_id,
                },
            )
            return
        self._capabilities[service_id] = capability
        self._rebuild_prefix_index()

    def _rebuild_prefix_index(self) -> None:
        index: dict[str, str] = {}
        for service_id, capability in self._capabilities.items():
            if capability.prefix in index:
                self._logger.error(
                    "prefix_conflict",
                    extra={
                        "prefix": capability.prefix,
                        "service_id": service_id,
                        "other_service_id": index[capability.prefix],
                    },
                )
                continue
            index[capability.prefix] = service_id
        self._prefix_index = index

    async def _listen(self) -> None:
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(
            CAPABILITIES_CHANGED_CHANNEL, keyspace_expired_channel(self._db_index)
        )
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                channel = str(message["channel"])
                data = str(message["data"])
                if channel == CAPABILITIES_CHANGED_CHANNEL:
                    await self.refresh_service(data)
                elif data.startswith(CAPABILITIES_KEY_PREFIX):
                    self.drop_service(data[len(CAPABILITIES_KEY_PREFIX) :])
        finally:
            await pubsub.aclose()


def resolve_display_name(service_id: str, config: Config, registry: CapabilityRegistry) -> str:
    override = config.service_display_names.get(service_id)
    if override:
        return override
    capability = registry.get(service_id)
    if capability is not None and capability.display_name:
        return capability.display_name
    return service_id
