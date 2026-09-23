from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from telegram_proxy.config import (
    CAPABILITY_TTL_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    SCHEMA_VERSION,
    inject_redis_password,
)
from telegram_proxy.keys import (
    CAPABILITIES_CHANGED_CHANNEL,
    OUTGOING_CHANNEL,
    capabilities_key,
    command_channel,
)

Handler = Callable[[dict[str, Any]], Awaitable[str | None]]


@dataclass(frozen=True)
class Parameter:
    type: str = "string"
    description: str = ""
    required: bool = False
    enum: list[str] | None = None
    extract: str = "model"


@dataclass
class _RegisteredCommand:
    spec: dict[str, Any]
    handler: Handler


def _parameter_spec(param: Parameter) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "type": param.type,
        "description": param.description,
        "required": param.required,
    }
    if param.enum is not None:
        spec["enum"] = list(param.enum)
    if param.extract != "model":
        spec["extract"] = param.extract
    return spec


class ServiceClient:
    def __init__(
        self,
        service_id: str,
        prefix: str,
        display_name: str | None = None,
        redis_url: str | None = None,
        redis: Redis | None = None,
        help: str | None = None,
        *,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        capability_ttl: int = CAPABILITY_TTL_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        if redis is None and redis_url is None:
            raise ValueError("redis_url or redis is required")
        self._redis = redis or Redis.from_url(
            inject_redis_password(redis_url, os.environ.get("REDIS_PASSWORD", "")),
            decode_responses=True,
        )
        self._service_id = service_id
        self._prefix = prefix
        self._display_name = display_name
        self._help = help
        self._heartbeat_interval = heartbeat_interval
        self._capability_ttl = capability_ttl
        self._logger = logger or logging.getLogger(__name__)
        self._commands: dict[str, _RegisteredCommand] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._pubsub: Any | None = None

    def command(
        self,
        name: str,
        *,
        description: str = "",
        examples: list[str] | None = None,
        usage: str | None = None,
        confirm: bool = False,
        parameters: dict[str, Parameter] | None = None,
    ) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            spec = {
                "name": name,
                "description": description,
                "examples": list(examples or []),
                "usage": usage,
                "confirm": confirm,
                "parameters": {
                    param_name: _parameter_spec(param)
                    for param_name, param in (parameters or {}).items()
                },
            }
            self._commands[name] = _RegisteredCommand(spec=spec, handler=handler)
            return handler

        return decorator

    def capability_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "service_id": self._service_id,
            "prefix": self._prefix,
            "display_name": self._display_name,
            "help": self._help,
            "commands": [registered.spec for registered in self._commands.values()],
        }

    async def start(self) -> None:
        await self._register()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(command_channel(self._service_id))

    async def run(self) -> None:
        await self.start()
        try:
            await self._listen()
        finally:
            await self.stop()

    async def stop(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None
        try:
            await self._redis.delete(capabilities_key(self._service_id))
            await self._redis.publish(CAPABILITIES_CHANGED_CHANNEL, self._service_id)
        finally:
            await self._redis.aclose()

    async def push(self, text: str, level: str = "info") -> None:
        payload = {"service_id": self._service_id, "text": text, "level": level}
        await self._redis.publish(OUTGOING_CHANNEL, json.dumps(payload))

    async def _register(self) -> None:
        await self._redis.set(
            capabilities_key(self._service_id),
            json.dumps(self.capability_payload()),
            ex=self._capability_ttl,
        )
        await self._redis.publish(CAPABILITIES_CHANGED_CHANNEL, self._service_id)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                await self._register()
            except Exception:
                self._logger.exception("heartbeat_failed")

    async def _listen(self) -> None:
        async for message in self._pubsub.listen():
            if message.get("type") != "message":
                continue
            await self._handle_command(str(message["data"]))

    async def _handle_command(self, raw: str) -> None:
        try:
            data = json.loads(raw)
            request_id = str(data["request_id"])
            name = str(data["command"])
            parameters = dict(data.get("parameters") or {})
        except (ValueError, KeyError, TypeError):
            self._logger.warning("command_rejected", extra={"raw": raw})
            return
        registered = self._commands.get(name)
        if registered is None:
            await self._reply(request_id, "error", f"unknown command '{name}'")
            return
        try:
            result = await registered.handler(parameters)
            text = result if result is not None else f"{name} done"
            await self._reply(request_id, "ok", text)
        except Exception as exc:
            self._logger.exception("command_failed", extra={"command": name})
            await self._reply(request_id, "error", f"command failed: {type(exc).__name__}: {exc}")

    async def _reply(self, request_id: str, status: str, text: str) -> None:
        payload = {
            "service_id": self._service_id,
            "request_id": request_id,
            "status": status,
            "text": text,
        }
        await self._redis.publish(OUTGOING_CHANNEL, json.dumps(payload))
