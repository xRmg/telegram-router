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
RESERVED_PREFIXES = frozenset({"help", "capabilities", "configured"})
DEFAULT_LLM_RATE_LIMIT_PER_MINUTE = 20
DEFAULT_NOTIFIER_RATE_LIMIT_PER_MINUTE = 10
DEFAULT_REDIS_URL = "redis://redis:6379/0"
DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_LLM_MODEL = "openrouter/auto"
DEFAULT_ROUTING_STRATEGY = "model"
ROUTING_STRATEGIES = ("model", "decision-select", "decision-extract", "decision-only")
_DECISION_STRATEGIES = frozenset(ROUTING_STRATEGIES[1:])
_EXTRACTION_STRATEGIES = frozenset({"decision-extract", "decision-only"})


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
    structured_decision_model: str = ""
    routing_strategy: str = DEFAULT_ROUTING_STRATEGY
    structured_decision_min_confidence: float = 0.5
    routing_verbose: bool = False
    routing_confidence_threshold: float = 0.6
    reply_timeout_seconds: float = 10.0
    llm_rate_limit_per_minute: int = DEFAULT_LLM_RATE_LIMIT_PER_MINUTE
    notifier_rate_limit_per_minute: int = DEFAULT_NOTIFIER_RATE_LIMIT_PER_MINUTE
    service_display_names: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.structured_decision_min_confidence <= 1.0:
            raise ConfigError("STRUCTURED_DECISION_MIN_CONFIDENCE must be between 0 and 1")
        if self.routing_strategy not in ROUTING_STRATEGIES:
            raise ConfigError(
                "ROUTING_STRATEGY must be one of: " + ", ".join(ROUTING_STRATEGIES)
            )
        if self.routing_strategy in _DECISION_STRATEGIES and not self.structured_decision_model:
            raise ConfigError(
                f"ROUTING_STRATEGY={self.routing_strategy} requires STRUCTURED_DECISION_MODEL"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        source = env if env is not None else os.environ
        redis_url = _optional(source, "REDIS_URL", DEFAULT_REDIS_URL)
        redis_password = _optional(source, "REDIS_PASSWORD", "")
        redis_url = inject_redis_password(redis_url, redis_password)
        return cls(
            telegram_bot_token=_required(source, "TELEGRAM_BOT_TOKEN"),
            telegram_owner_chat_id=_optional_int(source, "TELEGRAM_OWNER_CHAT_ID", None),
            llm_api_key=_optional(source, "LLM_API_KEY", ""),
            redis_url=redis_url,
            redis_password=redis_password,
            llm_base_url=_optional(source, "LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
            llm_model=_optional(source, "LLM_MODEL", DEFAULT_LLM_MODEL),
            structured_decision_model=_optional(source, "STRUCTURED_DECISION_MODEL", ""),
            routing_strategy=_optional(source, "ROUTING_STRATEGY", DEFAULT_ROUTING_STRATEGY),
            routing_verbose=_optional_bool(source, "ROUTING_VERBOSE", False),
            structured_decision_min_confidence=_optional_float(
                source, "STRUCTURED_DECISION_MIN_CONFIDENCE", 0.5
            ),
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
    def structured_decision_enabled(self) -> bool:
        return bool(
            self.llm_api_key
            and self.structured_decision_model
            and self.routing_strategy in _DECISION_STRATEGIES
        )

    @property
    def decision_extraction_enabled(self) -> bool:
        return self.routing_strategy in _EXTRACTION_STRATEGIES

    @property
    def chat_model_enabled(self) -> bool:
        return self.routing_strategy != "decision-only"

    @property
    def redis_db_index(self) -> int:
        path = urlparse(self.redis_url).path.lstrip("/")
        if not path:
            return 0
        try:
            return int(path.split("/", 1)[0])
        except ValueError:
            return 0


def inject_redis_password(redis_url: str, redis_password: str) -> str:
    if not redis_password or "@" in redis_url:
        return redis_url
    parsed = urlparse(redis_url)
    netloc = f":{quote(redis_password, safe='')}@{parsed.netloc}"
    return parsed._replace(netloc=netloc).geturl()


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


def _optional_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, "").strip().casefold()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be a boolean")


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
