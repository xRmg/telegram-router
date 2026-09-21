from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote, urlparse

SCHEMA_VERSION = 1
CAPABILITY_TTL_SECONDS = 90
HEARTBEAT_INTERVAL_SECONDS = 30
PENDING_CONFIRMATION_TTL_SECONDS = 60
RATE_LIMIT_WINDOW_SECONDS = 60
RESERVED_PREFIXES = frozenset({"help", "capabilities"})
DEFAULT_LLM_RATE_LIMIT_PER_MINUTE = 20
DEFAULT_NOTIFIER_RATE_LIMIT_PER_MINUTE = 10
DEFAULT_REDIS_URL = "redis://redis:6379/0"
DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_LLM_MODEL = "openrouter/auto"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_owner_chat_id: int | None = None
    learn_token: str = ""
    llm_api_key: str = ""
    redis_url: str = DEFAULT_REDIS_URL
    redis_password: str = ""
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_model: str = DEFAULT_LLM_MODEL
    routing_confidence_threshold: float = 0.6
    reply_timeout_seconds: float = 10.0
    llm_rate_limit_per_minute: int = DEFAULT_LLM_RATE_LIMIT_PER_MINUTE
    notifier_rate_limit_per_minute: int = DEFAULT_NOTIFIER_RATE_LIMIT_PER_MINUTE
    service_display_names: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        source = env if env is not None else os.environ
        redis_url = _optional(source, "REDIS_URL", DEFAULT_REDIS_URL)
        redis_password = _optional(source, "REDIS_PASSWORD", "")
        if redis_password and "@" not in redis_url:
            parsed = urlparse(redis_url)
            netloc = f"proxy:{quote(redis_password, safe='')}@{parsed.netloc}"
            redis_url = parsed._replace(netloc=netloc).geturl()
        return cls(
            telegram_bot_token=_required(source, "TELEGRAM_BOT_TOKEN"),
            telegram_owner_chat_id=_optional_int(source, "TELEGRAM_OWNER_CHAT_ID", None),
            llm_api_key=_optional(source, "LLM_API_KEY", ""),
            redis_url=redis_url,
            redis_password=redis_password,
            llm_base_url=_optional(source, "LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
            llm_model=_optional(source, "LLM_MODEL", DEFAULT_LLM_MODEL),
            routing_confidence_threshold=_optional_float(
                source, "ROUTING_CONFIDENCE_THRESHOLD", 0.6
            ),
            reply_timeout_seconds=_optional_float(source, "REPLY_TIMEOUT_SECONDS", 10.0),
            llm_rate_limit_per_minute=_optional_int(
                source, "LLM_RATE_LIMIT_PER_MINUTE", DEFAULT_LLM_RATE_LIMIT_PER_MINUTE
            ),
            notifier_rate_limit_per_minute=_optional_int(
                source,
                "NOTIFIER_RATE_LIMIT_PER_MINUTE",
                DEFAULT_NOTIFIER_RATE_LIMIT_PER_MINUTE,
            ),
            service_display_names=_optional_json_dict(source, "SERVICE_DISPLAY_NAMES"),
        )

    @property
    def learn_owner_mode(self) -> bool:
        return self.telegram_owner_chat_id is None

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def redis_db_index(self) -> int:
        path = urlparse(self.redis_url).path.lstrip("/")
        if not path:
            return 0
        try:
            return int(path.split("/", 1)[0])
        except ValueError:
            return 0


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ConfigError(f"{key} is required")
    return value


def _required_int(env: Mapping[str, str], key: str) -> int:
    raw = _required(env, key)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc


def _optional(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key, "").strip()
    return value or default


def _optional_int(env: Mapping[str, str], key: str, default: int | None) -> int | None:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc


def _optional_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc


def _optional_json_dict(env: Mapping[str, str], key: str) -> dict[str, str]:
    raw = env.get(key, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{key} must be valid JSON") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{key} must be a JSON object")
    return {str(name): str(value) for name, value in data.items()}
