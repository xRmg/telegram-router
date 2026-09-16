from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from .commands import CommandParseError, validate_arguments
from .config import Config
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
class RouteOutcome:
    decision: RouteDecision | None
    raw_response: str = ""
    error: str | None = None


class ToolRouter:
    def __init__(
        self,
        config: Config,
        registry: CapabilityRegistry,
        logger: logging.Logger | None = None,
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._logger = logger or logging.getLogger(__name__)
        self._client = client

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
        tools, index = self.build_tools()
        if not tools:
            return RouteOutcome(decision=None, error="no_tools")
        response = await self._openai_client().chat.completions.create(
            model=self._config.llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            tools=tools,
            tool_choice="auto",
            temperature=0,
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
        confidence = _parse_confidence(arguments.pop("confidence", None))
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


def _parse_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(confidence, 0.0), 1.0)
