from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import Capability, CommandSpec

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_PAIR_START = re.compile(r"\S+=")


class CommandParseError(ValueError):
    pass


@dataclass(frozen=True)
class ExplicitCommand:
    prefix: str
    rest: str


def parse_explicit(text: str) -> ExplicitCommand | None:
    stripped = text.strip()
    if not stripped or stripped[0] not in {"/", "@"}:
        return None
    head, _, rest = stripped.partition(" ")
    token = head[1:]
    prefix = token.split("@", 1)[0].casefold()
    if not prefix:
        return None
    return ExplicitCommand(prefix=prefix, rest=rest.strip())


def parse_command(rest: str, capability: Capability) -> tuple[CommandSpec, dict[str, Any]]:
    head, _, remainder = rest.partition(" ")
    if not head:
        raise CommandParseError("missing command name")
    command = capability.command_by_name(head)
    if command is None:
        raise CommandParseError(f"unknown command '{head}'")
    raw = _parse_pairs(remainder.strip())
    parameters = coerce_parameters(command, raw)
    return command, parameters


def coerce_parameters(command: CommandSpec, raw: dict[str, str]) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for name, value in raw.items():
        spec = command.parameters.get(name)
        if spec is None:
            raise CommandParseError(f"unknown parameter '{name}'")
        parameters[name] = coerce_value(value, spec.type)
    _check_required(command, parameters)
    return parameters


def validate_arguments(command: CommandSpec, arguments: dict[str, Any]) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for name, spec in command.parameters.items():
        if name in arguments and arguments[name] is not None:
            parameters[name] = _coerce_typed(arguments[name], spec.type)
    _check_required(command, parameters)
    return parameters


def coerce_value(value: str, type_: str) -> Any:
    if type_ == "number":
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError as exc:
            raise CommandParseError(f"'{value}' is not a number") from exc
    if type_ == "boolean":
        lowered = value.casefold()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
        raise CommandParseError(f"'{value}' is not a boolean")
    return value


def _coerce_typed(value: Any, type_: str) -> Any:
    if type_ == "number":
        if isinstance(value, bool):
            raise CommandParseError("boolean is not a number")
        if isinstance(value, (int, float)):
            return value
        return coerce_value(str(value), type_)
    if type_ == "boolean":
        if isinstance(value, bool):
            return value
        return coerce_value(str(value), type_)
    return str(value)


def _check_required(command: CommandSpec, parameters: dict[str, Any]) -> None:
    missing = [
        name
        for name, spec in command.parameters.items()
        if spec.required and name not in parameters
    ]
    if missing:
        raise CommandParseError("missing required parameter(s): " + ", ".join(missing))


def _parse_pairs(rest: str) -> dict[str, str]:
    if not rest:
        return {}
    matches = list(_PAIR_START.finditer(rest))
    if not matches:
        raise CommandParseError("parameters must use key=value form")
    pairs: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(rest)
        token = rest[match.start() : end].rstrip()
        key, value = token.split("=", 1)
        if not key:
            raise CommandParseError(f"parameter '{token}' must use key=value form")
        pairs[key] = value.strip()
    return pairs
