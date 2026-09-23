from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .config import RESERVED_PREFIXES, SCHEMA_VERSION

ParameterType = Literal["string", "number", "boolean"]
ExtractMode = Literal["model", "span"]
_PREFIX_PATTERN = re.compile(r"[a-z0-9_-]+")


class ParameterSpec(BaseModel):
    type: ParameterType = "string"
    description: str = ""
    required: bool = False
    enum: list[str] | None = None
    extract: ExtractMode = "model"

    @field_validator("enum")
    @classmethod
    def _validate_enum(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("enum must not be empty")
        if len(value) != len({item.casefold() for item in value}):
            raise ValueError("enum values must be unique")
        return value

    @property
    def decision_extractable(self) -> bool:
        return self.enum is not None or self.extract == "span"


class CommandSpec(BaseModel):
    name: str
    description: str = ""
    examples: list[str] = Field(default_factory=list)
    usage: str | None = None
    confirm: bool = False
    parameters: dict[str, ParameterSpec] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("command name must not be empty")
        return value

    @property
    def decision_routable(self) -> bool:
        return all(spec.decision_extractable for spec in self.parameters.values())


class Capability(BaseModel):
    schema_version: int = SCHEMA_VERSION
    service_id: str
    prefix: str
    display_name: str | None = None
    help: str | None = None
    commands: list[CommandSpec] = Field(default_factory=list)

    @field_validator("service_id")
    @classmethod
    def _validate_service_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("service_id must not be empty")
        return value

    @field_validator("prefix")
    @classmethod
    def _validate_prefix(cls, value: str) -> str:
        if not _PREFIX_PATTERN.fullmatch(value):
            raise ValueError("prefix must match [a-z0-9_-]+")
        if value in RESERVED_PREFIXES:
            raise ValueError(f"prefix '{value}' is reserved")
        return value

    @model_validator(mode="after")
    def _validate_contents(self) -> Capability:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {self.schema_version}")
        names = [command.name for command in self.commands]
        if len(names) != len(set(names)):
            raise ValueError("command names must be unique")
        for command in self.commands:
            if "confidence" in command.parameters:
                raise ValueError("parameter name 'confidence' is reserved")
        return self

    def command_by_name(self, name: str) -> CommandSpec | None:
        wanted = name.casefold()
        for command in self.commands:
            if command.name.casefold() == wanted:
                return command
        return None


class CommandMessage(BaseModel):
    request_id: str
    command: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class OutgoingMessage(BaseModel):
    service_id: str
    text: str
    request_id: str | None = None
    status: Literal["ok", "error"] | None = None
    level: Literal["info", "warning", "critical"] = "info"

    @model_validator(mode="after")
    def _validate_status(self) -> OutgoingMessage:
        if self.request_id is not None and self.status is None:
            raise ValueError("status is required when request_id is present")
        if self.request_id is None and self.status is not None:
            raise ValueError("status is only valid when request_id is present")
        return self
