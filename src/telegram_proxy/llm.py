from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
from openai import AsyncOpenAI

from .commands import CommandParseError, validate_arguments
from .config import Config
from .decisions import (
    ABSENT_OPTION,
    DECISIONS_PATH,
    NO_MATCH_OPTION,
    build_candidates,
    capability_question,
    choice_answer,
    decisions_base_url,
    parameter_question,
    request_payload,
)
from .models import CommandSpec
from .registry import CapabilityRegistry

SYSTEM_PROMPT = (
    "You route a user's message to at most one registered tool. "
    "Call the single tool that best matches the user's intent and set the required "
    "confidence argument to your certainty between 0 and 1. "
    "Use a low confidence when the match is uncertain. "
    "Only pass parameter values the user actually implied. "
    "If no tool fits the message, do not call any tool."
)


@dataclass(frozen=True)
class RouteDecision:
    service_id: str
    command: str
    parameters: dict[str, Any]
    confidence: float
    tool_name: str


@dataclass(frozen=True)
class RouteStep:
    stage: str
    model: str
    seconds: float
    cost: float | None = None


@dataclass(frozen=True)
class RouteOutcome:
    decision: RouteDecision | None
    raw_response: str = ""
    error: str | None = None
    steps: tuple[RouteStep, ...] = field(default_factory=tuple)

    @property
    def cost(self) -> float | None:
        costs = [step.cost for step in self.steps if step.cost is not None]
        return sum(costs) if costs else None

    @property
    def seconds(self) -> float:
        return sum(step.seconds for step in self.steps)

    @property
    def models(self) -> list[str]:
        return [step.model for step in self.steps]


class ToolRouter:
    def __init__(
        self,
        config: Config,
        registry: CapabilityRegistry,
        logger: logging.Logger | None = None,
        client: Any | None = None,
        decisions_client: Any | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._logger = logger or logging.getLogger(__name__)
        self._client = client
        self._decisions_client = decisions_client

    def build_tools(self) -> tuple[list[dict[str, Any]], dict[str, tuple[str, CommandSpec]]]:
        tools: list[dict[str, Any]] = []
        index: dict[str, tuple[str, CommandSpec]] = {}
        for capability in self._registry.all():
            for command in capability.commands:
                tool_name = f"{capability.service_id}.{command.name}"
                properties: dict[str, Any] = {}
                required: list[str] = []
                for name, spec in command.parameters.items():
                    properties[name] = {"type": spec.type, "description": spec.description}
                    if spec.required:
                        required.append(name)
                properties["confidence"] = {
                    "type": "number",
                    "description": (
                        "Certainty from 0 to 1 that this tool matches the user's message."
                    ),
                }
                required.append("confidence")
                description = command.description or command.name
                if command.examples:
                    description = f"{description} Examples: " + "; ".join(command.examples)
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "description": description,
                            "parameters": {
                                "type": "object",
                                "properties": properties,
                                "required": required,
                            },
                        },
                    }
                )
                index[tool_name] = (capability.service_id, command)
        return tools, index

    async def route(self, text: str) -> RouteOutcome:
        steps: list[RouteStep] = []
        outcome = await self._route(text, steps)
        return replace(outcome, steps=tuple(steps))

    async def _route(self, text: str, steps: list[RouteStep]) -> RouteOutcome:
        tools, index = self.build_tools()
        if not tools:
            return RouteOutcome(decision=None, error="no_tools")
        if self._config.structured_decision_enabled:
            return await self._route_with_decision_model(text, tools, index, steps)
        return await self._call_chat_model(text, tools, index, "auto", steps)

    async def _route_with_decision_model(
        self,
        text: str,
        tools: list[dict[str, Any]],
        index: dict[str, tuple[str, CommandSpec]],
        steps: list[RouteStep],
    ) -> RouteOutcome:
        tool_by_name = {tool["function"]["name"]: tool for tool in tools}
        criteria = {name: tool["function"]["description"] for name, tool in tool_by_name.items()}
        try:
            data = await self._decisions_request(
                {"message": text},
                {"capability": capability_question(criteria)},
                "capability",
                steps,
            )
        except httpx.HTTPError as exc:
            self._logger.warning("decision_model_call_failed", extra={"error": str(exc)})
            return RouteOutcome(decision=None, error="decision_model_error")
        raw_response = json.dumps(data)
        tool_name, confidence = choice_answer(data, "capability")
        if tool_name is None or tool_name == NO_MATCH_OPTION or tool_name not in index:
            return RouteOutcome(decision=None, raw_response=raw_response)
        service_id, command = index[tool_name]
        if self._config.decision_extraction_enabled and command.decision_routable:
            outcome = await self._extract_parameters(
                text, tool_name, service_id, command, confidence, raw_response, steps
            )
            if outcome is not None:
                return outcome
        elif not command.parameters:
            return RouteOutcome(
                decision=_decision(service_id, command, {}, confidence, tool_name),
                raw_response=raw_response,
            )
        if not self._config.chat_model_enabled:
            self._logger.info(
                "extraction_unavailable",
                extra={"tool_name": tool_name, "strategy": self._config.routing_strategy},
            )
            return RouteOutcome(
                decision=None, raw_response=raw_response, error="extraction_unavailable"
            )
        return await self._call_chat_model(
            text,
            [tool_by_name[tool_name]],
            index,
            {"type": "function", "function": {"name": tool_name}},
            steps,
            override_confidence=confidence,
        )

    async def _extract_parameters(
        self,
        text: str,
        tool_name: str,
        service_id: str,
        command: CommandSpec,
        confidence: float,
        raw_response: str,
        steps: list[RouteStep],
    ) -> RouteOutcome | None:
        if not command.parameters:
            return RouteOutcome(
                decision=_decision(service_id, command, {}, confidence, tool_name),
                raw_response=raw_response,
            )
        candidates, over_cap = build_candidates(text)
        if over_cap:
            self._log_fallback(tool_name, "candidate_cap")
            return None
        options_by_name = {
            name: (spec.enum if spec.enum is not None else candidates)
            for name, spec in command.parameters.items()
        }
        questions = {
            name: parameter_question(tool_name, name, spec, options_by_name[name])
            for name, spec in command.parameters.items()
        }
        try:
            data = await self._decisions_request(text, questions, "parameters", steps)
        except httpx.HTTPError as exc:
            self._logger.warning("decision_model_call_failed", extra={"error": str(exc)})
            return None
        arguments: dict[str, Any] = {}
        for name in command.parameters:
            choice, param_confidence = choice_answer(data, name)
            if choice is None or param_confidence < self._config.structured_decision_min_confidence:
                self._log_fallback(tool_name, "low_confidence")
                return None
            if choice == ABSENT_OPTION:
                continue
            if choice not in options_by_name[name]:
                self._log_fallback(tool_name, "unknown_option")
                return None
            arguments[name] = choice
        extraction_response = json.dumps(data)
        try:
            parameters = validate_arguments(command, arguments)
        except CommandParseError as exc:
            self._logger.warning(
                "decision_invalid_arguments",
                extra={"tool_name": tool_name, "error": str(exc)},
            )
            return RouteOutcome(
                decision=None, raw_response=extraction_response, error="invalid_arguments"
            )
        return RouteOutcome(
            decision=_decision(service_id, command, parameters, confidence, tool_name),
            raw_response=extraction_response,
        )

    def _log_fallback(self, tool_name: str, reason: str) -> None:
        self._logger.info(
            "parameter_extraction_fallback",
            extra={"tool_name": tool_name, "reason": reason},
        )

    async def _decisions_request(
        self,
        state: Any,
        questions: dict[str, Any],
        stage: str,
        steps: list[RouteStep],
    ) -> dict[str, Any]:
        start = time.perf_counter()
        response = await self._decisions_http_client().post(
            DECISIONS_PATH,
            json=request_payload(self._config.structured_decision_model, state, questions),
        )
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage") or {}
        steps.append(
            RouteStep(
                stage=stage,
                model=data.get("model") or self._config.structured_decision_model,
                seconds=time.perf_counter() - start,
                cost=_coerce_cost(usage.get("cost")),
            )
        )
        return data

    async def _call_chat_model(
        self,
        text: str,
        tools: list[dict[str, Any]],
        index: dict[str, tuple[str, CommandSpec]],
        tool_choice: Any,
        steps: list[RouteStep],
        override_confidence: float | None = None,
    ) -> RouteOutcome:
        start = time.perf_counter()
        response = await self._openai_client().chat.completions.create(
            model=self._config.llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            tools=tools,
            tool_choice=tool_choice,
            temperature=0,
        )
        steps.append(
            RouteStep(
                stage="chat",
                model=getattr(response, "model", None) or self._config.llm_model,
                seconds=time.perf_counter() - start,
                cost=_usage_cost(getattr(response, "usage", None)),
            )
        )
        message = response.choices[0].message
        raw_response = message.model_dump_json()
        tool_calls = message.tool_calls or []
        if not tool_calls:
            return RouteOutcome(decision=None, raw_response=raw_response)
        call = tool_calls[0]
        target = index.get(call.function.name)
        if target is None:
            self._logger.warning("llm_unknown_tool", extra={"tool_name": call.function.name})
            return RouteOutcome(decision=None, raw_response=raw_response, error="unknown_tool")
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            return RouteOutcome(decision=None, raw_response=raw_response, error="invalid_arguments")
        if not isinstance(arguments, dict):
            return RouteOutcome(decision=None, raw_response=raw_response, error="invalid_arguments")
        if override_confidence is None:
            confidence = _parse_confidence(arguments.pop("confidence", None))
        else:
            arguments.pop("confidence", None)
            confidence = override_confidence
        service_id, command = target
        try:
            parameters = validate_arguments(command, arguments)
        except CommandParseError as exc:
            self._logger.warning(
                "llm_invalid_arguments",
                extra={"tool_name": call.function.name, "error": str(exc)},
            )
            return RouteOutcome(decision=None, raw_response=raw_response, error="invalid_arguments")
        decision = RouteDecision(
            service_id=service_id,
            command=command.name,
            parameters=parameters,
            confidence=confidence,
            tool_name=call.function.name,
        )
        return RouteOutcome(decision=decision, raw_response=raw_response)

    def _openai_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=self._config.llm_base_url, api_key=self._config.llm_api_key
            )
        return self._client

    def _decisions_http_client(self) -> Any:
        if self._decisions_client is None:
            self._decisions_client = httpx.AsyncClient(
                base_url=decisions_base_url(self._config.llm_base_url),
                headers={"Authorization": f"Bearer {self._config.llm_api_key}"},
                timeout=10.0,
            )
        return self._decisions_client


def _parse_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(confidence, 0.0), 1.0)


def _coerce_cost(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _usage_cost(usage: Any) -> float | None:
    if usage is None:
        return None
    cost = getattr(usage, "cost", None)
    if cost is None:
        extra = getattr(usage, "model_extra", None)
        if isinstance(extra, dict):
            cost = extra.get("cost")
    return _coerce_cost(cost)


def _decision(
    service_id: str,
    command: CommandSpec,
    parameters: dict[str, Any],
    confidence: float,
    tool_name: str,
) -> RouteDecision:
    return RouteDecision(
        service_id=service_id,
        command=command.name,
        parameters=parameters,
        confidence=confidence,
        tool_name=tool_name,
    )
