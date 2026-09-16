from __future__ import annotations

CAPABILITIES_KEY_PREFIX = "capabilities:"
CAPABILITIES_CHANGED_CHANNEL = "capabilities:changed"
PENDING_KEY_PREFIX = "pending:"
LAST_UPDATE_ID_KEY = "telegram:last_update_id"
OWNER_CHAT_ID_KEY = "telegram:owner_chat_id"
COMMAND_CHANNEL_PREFIX = "cmd:"
OUTGOING_CHANNEL = "telegram:outgoing"
LLM_RATE_LIMIT_KEY = "ratelimit:llm"
NOTIFIER_RATE_LIMIT_PREFIX = "ratelimit:notify:"


def capabilities_key(service_id: str) -> str:
    return f"{CAPABILITIES_KEY_PREFIX}{service_id}"


def pending_key(confirmation_id: str) -> str:
    return f"{PENDING_KEY_PREFIX}{confirmation_id}"


def command_channel(service_id: str) -> str:
    return f"{COMMAND_CHANNEL_PREFIX}{service_id}"


def notifier_rate_limit_key(service_id: str) -> str:
    return f"{NOTIFIER_RATE_LIMIT_PREFIX}{service_id}"


def keyspace_expired_channel(db_index: int) -> str:
    return f"__keyevent@{db_index}__:expired"
