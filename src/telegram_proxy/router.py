from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass
from typing import Any

from .commands import CommandParseError, ExplicitCommand, parse_command, parse_explicit
from .config import PENDING_CONFIRMATION_TTL_SECONDS, Config
from .help_text import build_service_help
from .keys import LLM_RATE_LIMIT_KEY, command_channel, pending_key
from .llm import RouteOutcome, ToolRouter
from .models import CommandMessage, OutgoingMessage
from .notifier import Notifier
from .ratelimit import FixedWindowLimiter
from .registry import CapabilityRegistry, resolve_display_name

UNSURE_MESSAGE = (
    "I'm not sure which service should handle that. Try /help to see what is available."
)
LLM_BUSY_MESSAGE = "Too many requests right now — try again in a minute."
NO_SERVICES_MESSAGE = "No services with commands are registered yet."
LLM_DISABLED_MESSAGE = "Free-text routing is not configured yet. Use /<prefix> <command> instead."
EXTRACTION_UNAVAILABLE_MESSAGE = (
    "I know which service that is, but I can't read the details out of free text. "
    "Use /<prefix> <command> key=value — /help shows the exact form."
)


@dataclass
class _PendingReply:
    service_id: str
    command: str
    confidence: float | None
    origin: str
    timeout_task: asyncio.Task[None]


def _verbose_trace(outcome: RouteOutcome) -> str:
    if not outcome.steps:
        return "Routing trace: no model calls."
    lines = ["Routing trace:"]
    for step in outcome.steps:
        cost = f" · ${step.cost:.6f}" if step.cost is not None else ""
        lines.append(f"  {step.stage}: {step.model} ({step.seconds:.2f}s{cost})")
    total = f"  total {outcome.seconds:.2f}s"
    if outcome.cost is not None:
        total += f" · ${outcome.cost:.6f}"
    lines.append(total)
    decision = outcome.decision
    if decision is None:
        lines.append(f"  -> no match ({outcome.error or 'none'})")
    else:
        lines.append(
            f"  -> {decision.tool_name} {decision.parameters} "
            f"(confidence {decision.confidence:.2f})"
        )
    return "\n".join(lines)


def new_request_id() -> str:
    return secrets.token_hex(3)


def new_confirmation_id() -> str:
    return secrets.token_hex(4)


class Router:
    def __init__(
        self,
        redis: Any,
        registry: CapabilityRegistry,
        config: Config,
        notifier: Notifier,
        llm: ToolRouter,
        limiter: FixedWindowLimiter,
        logger: logging.Logger | None = None,
    ) -> None:
        self._redis = redis
        self._registry = registry
        self._config = config
        self._notifier = notifier
        self._llm = llm
        self._limiter = limiter
        self._logger = logger or logging.getLogger(__name__)
        self._pending: dict[str, _PendingReply] = {}

    async def handle_text(self, text: str) -> None:
        explicit = parse_explicit(text)
        if explicit is not None:
            await self._handle_explicit(explicit)
        else:
            await self._handle_free_text(text)

    async def handle_confirmation(self, confirmation_id: str, approved: bool) -> str:
        key = pending_key(confirmation_id)
        raw = await self._redis.get(key)
        if raw is None:
            self._log(new_request_id(), None, None, None, "confirmation", "confirmation_expired")
            return "expired"
        if not approved:
            await self._redis.delete(key)
            self._log(new_request_id(), None, None, None, "confirmation", "confirmation_cancelled")
            return "cancelled"
        try:
            data = json.loads(raw)
            service_id = str(data["service_id"])
            command = str(data["command"])
            parameters = dict(data.get("parameters") or {})
        except (ValueError, KeyError, TypeError):
            await self._redis.delete(key)
            return "expired"
        await self._redis.delete(key)
        return await self._dispatch(
            service_id,
            command,
            parameters,
            confidence=None,
            origin="confirmation",
            confirmed=True,
        )

    async def resolve_reply(self, message: OutgoingMessage) -> bool:
        pending = self._pending.pop(message.request_id, None)
        if pending is None:
            return False
        pending.timeout_task.cancel()
        if message.service_id != pending.service_id:
            self._logger.warning(
                "reply_service_mismatch",
                extra={
                    "request_id": message.request_id,
                    "expected_service_id": pending.service_id,
                    "actual_service_id": message.service_id,
                },
            )
        outcome = "replied" if message.status == "ok" else "reply_error"
        self._log(
            message.request_id,
            pending.service_id,
            pending.command,
            pending.confidence,
            pending.origin,
            outcome,
        )
        return True

    async def _handle_explicit(self, explicit: ExplicitCommand) -> None:
        capability = self._registry.by_prefix(explicit.prefix)
        if capability is None:
            await self._notifier.send(f"Unknown service '/{explicit.prefix}'. Try /help.")
            return
        name = resolve_display_name(capability.service_id, self._config, self._registry)
        if not explicit.rest:
            if len(capability.commands) == 1:
                rest = capability.commands[0].name
            else:
                await self._notifier.send(
                    build_service_help(self._config, self._registry, capability.prefix)
                )
                return
        else:
            rest = explicit.rest
        try:
            command, parameters = parse_command(rest, capability)
        except CommandParseError as exc:
            await self._notifier.send(f"{name}: {exc}.")
            return
        await self._dispatch(
            capability.service_id,
            command.name,
            parameters,
            confidence=None,
            origin="explicit",
        )

    async def _handle_free_text(self, text: str) -> None:
        if not self._config.llm_enabled:
            self._log(new_request_id(), None, None, None, "llm", "llm_disabled")
            await self._notifier.send(LLM_DISABLED_MESSAGE)
            return
        if not self._registry.has_routable_commands():
            await self._notifier.send(NO_SERVICES_MESSAGE)
            return
        if not await self._limiter.hit(LLM_RATE_LIMIT_KEY, self._config.llm_rate_limit_per_minute):
            self._log(new_request_id(), None, None, None, "llm", "llm_rate_limited")
            await self._notifier.send(LLM_BUSY_MESSAGE)
            return
        sensitive: dict[str, object] = {"sensitive_user_message": text}
        try:
            outcome = await self._llm.route(text)
        except Exception as exc:
            self._logger.exception("llm_call_failed", extra={"error": str(exc)})
            self._log(new_request_id(), None, None, None, "llm", "llm_error", sensitive)
            await self._notifier.send(UNSURE_MESSAGE)
            return
        if outcome.raw_response:
            sensitive["sensitive_llm_response"] = outcome.raw_response
        if outcome.steps:
            sensitive["models"] = [f"{step.stage}={step.model}" for step in outcome.steps]
            sensitive["cost_usd"] = outcome.cost
            sensitive["routing_seconds"] = round(outcome.seconds, 3)
        if self._config.routing_verbose:
            await self._notifier.send(_verbose_trace(outcome))
        decision = outcome.decision
        if decision is None:
            self._log(
                new_request_id(),
                None,
                None,
                None,
                "llm",
                outcome.error or "no_match",
                sensitive,
            )
            if outcome.error == "extraction_unavailable":
                await self._notifier.send(EXTRACTION_UNAVAILABLE_MESSAGE)
            else:
                await self._notifier.send(UNSURE_MESSAGE)
            return
        if decision.confidence < self._config.routing_confidence_threshold:
            self._log(
                new_request_id(),
                decision.service_id,
                decision.command,
                decision.confidence,
                "llm",
                "low_confidence",
                sensitive,
            )
            await self._notifier.send(UNSURE_MESSAGE)
            return
        await self._dispatch(
            decision.service_id,
            decision.command,
            decision.parameters,
            confidence=decision.confidence,
            origin="llm",
            sensitive=sensitive,
        )

    async def _dispatch(
        self,
        service_id: str,
        command_name: str,
        parameters: dict[str, Any],
        confidence: float | None,
        origin: str,
        request_id: str | None = None,
        sensitive: dict[str, object] | None = None,
        confirmed: bool = False,
    ) -> str:
        request_id = request_id or new_request_id()
        capability = self._registry.get(service_id)
        if capability is None or not await self._registry.is_live(service_id):
            await self._notifier.send(f"{self._name(service_id)} is not available right now.")
            self._log(
                request_id,
                service_id,
                command_name,
                confidence,
                origin,
                "service_unavailable",
                sensitive,
            )
            return "service_unavailable"
        command = capability.command_by_name(command_name)
        if command is None:
            await self._notifier.send(
                f"{self._name(service_id)}: unknown command '{command_name}'. Try /help."
            )
            self._log(
                request_id,
                service_id,
                command_name,
                confidence,
                origin,
                "unknown_command",
                sensitive,
            )
            return "unknown_command"
        if command.confirm and not confirmed:
            confirmation_id = new_confirmation_id()
            payload = json.dumps(
                {"service_id": service_id, "command": command.name, "parameters": parameters}
            )
            await self._redis.set(
                pending_key(confirmation_id),
                payload,
                ex=PENDING_CONFIRMATION_TTL_SECONDS,
            )
            await self._notifier.send_confirmation(
                self._confirmation_text(service_id, command.name, parameters),
                confirmation_id,
            )
            self._log(
                request_id,
                service_id,
                command.name,
                confidence,
                origin,
                "confirmation_requested",
                sensitive,
            )
            return "confirmation_requested"
        await self._publish(service_id, request_id, command.name, parameters)
        timeout_task = asyncio.create_task(self._reply_timeout(request_id))
        self._pending[request_id] = _PendingReply(
            service_id=service_id,
            command=command.name,
            confidence=confidence,
            origin=origin,
            timeout_task=timeout_task,
        )
        self._log(request_id, service_id, command.name, confidence, origin, "dispatched", sensitive)
        return "dispatched"

    async def _publish(
        self,
        service_id: str,
        request_id: str,
        command: str,
        parameters: dict[str, Any],
    ) -> None:
        message = CommandMessage(request_id=request_id, command=command, parameters=parameters)
        await self._redis.publish(command_channel(service_id), message.model_dump_json())

    async def _reply_timeout(self, request_id: str) -> None:
        try:
            await asyncio.sleep(self._config.reply_timeout_seconds)
        except asyncio.CancelledError:
            return
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        await self._notifier.send(
            f"{self._name(pending.service_id)}: no reply within "
            f"{self._config.reply_timeout_seconds:g}s."
        )
        self._log(
            request_id,
            pending.service_id,
            pending.command,
            pending.confidence,
            pending.origin,
            "timeout",
        )

    def _confirmation_text(self, service_id: str, command: str, parameters: dict[str, Any]) -> str:
        rendered = ", ".join(f"{name}={value}" for name, value in parameters.items())
        detail = f" {rendered}" if rendered else ""
        return f"Confirm {self._name(service_id)}: {command}{detail}?"

    def _name(self, service_id: str) -> str:
        return resolve_display_name(service_id, self._config, self._registry)

    def _log(
        self,
        request_id: str,
        service_id: str | None,
        command: str | None,
        confidence: float | None,
        origin: str | None,
        outcome: str,
        sensitive: dict[str, object] | None = None,
    ) -> None:
        extra: dict[str, object] = {"request_id": request_id, "outcome": outcome}
        if origin is not None:
            extra["origin"] = origin
        if service_id is not None:
            extra["service_id"] = service_id
        if command is not None:
            extra["command"] = command
        if confidence is not None:
            extra["confidence"] = confidence
        if sensitive:
            extra.update(sensitive)
        self._logger.info("routing_decision", extra=extra)
