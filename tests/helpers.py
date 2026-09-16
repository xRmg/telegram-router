from telegram_proxy.config import Config
from telegram_proxy.keys import capabilities_key
from telegram_proxy.llm import RouteOutcome
from telegram_proxy.models import Capability


def make_config(**overrides) -> Config:
    values = {
        "telegram_bot_token": "token",
        "telegram_owner_chat_id": 42,
        "llm_api_key": "key",
        "reply_timeout_seconds": 0.05,
        "routing_confidence_threshold": 0.6,
    }
    values.update(overrides)
    return Config(**values)


def make_capability(**overrides) -> Capability:
    values = {
        "schema_version": 1,
        "service_id": "home-automation",
        "prefix": "ha",
        "display_name": "HA",
        "commands": [
            {
                "name": "lights_on",
                "description": "Turn on the lights in a room",
                "examples": ["turn on the lights"],
                "usage": "lights_on room=living room",
                "parameters": {
                    "room": {"type": "string", "description": "Room name", "required": True}
                },
            },
            {
                "name": "lights_off",
                "description": "Turn off the lights in a room",
                "parameters": {
                    "room": {"type": "string", "description": "Room name", "required": True}
                },
            },
            {
                "name": "lock_door",
                "description": "Lock the front door",
                "examples": ["lock the door"],
                "confirm": True,
            },
        ],
    }
    values.update(overrides)
    return Capability.model_validate(values)


async def put_capability(redis, capability: Capability) -> None:
    await redis.set(capabilities_key(capability.service_id), capability.model_dump_json())


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.confirmations: list[tuple[str, str]] = []
        self.edits: list[tuple[int, str]] = []
        self.answers: list[tuple[str, str | None]] = []

    async def send(self, text: str, level: str = "info") -> int:
        self.sent.append((text, level))
        return len(self.sent)

    async def send_confirmation(self, text: str, confirmation_id: str) -> int:
        self.confirmations.append((text, confirmation_id))
        return 100 + len(self.confirmations)

    async def edit(self, message_id: int, text: str) -> None:
        self.edits.append((message_id, text))

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.answers.append((callback_id, text))


class FakeLLM:
    def __init__(self, outcome: RouteOutcome | None = None, error: Exception | None = None) -> None:
        self._outcome = outcome or RouteOutcome(decision=None)
        self._error = error

    async def route(self, text: str) -> RouteOutcome:
        if self._error is not None:
            raise self._error
        return self._outcome
