import pytest

from telegram_proxy.config import Config, ConfigError
from telegram_proxy.help_text import build_configuration_text
from tests.helpers import make_capability, make_config


class StubRegistry:
    def __init__(self, capabilities=None):
        self._capabilities = list(capabilities or [])

    def all(self):
        return list(self._capabilities)

    def get(self, service_id):
        for capability in self._capabilities:
            if capability.service_id == service_id:
                return capability
        return None


def _enum_capability():
    return make_capability(
        commands=[
            {
                "name": "lights_on",
                "description": "Turn on the lights",
                "parameters": {
                    "room": {"type": "string", "required": True, "enum": ["kitchen"]}
                },
            },
            {"name": "lock_door", "description": "Lock the door"},
            {
                "name": "remind",
                "description": "Remind at a time",
                "parameters": {"when": {"type": "string", "required": True}},
            },
        ]
    )


def test_reports_disabled_without_api_key():
    config = make_config(llm_api_key="")
    text = build_configuration_text(config, StubRegistry([make_capability()]))
    assert "API key: not set" in text
    assert "Free-text routing: disabled" in text
    assert "Explicit commands" in text


def test_reports_free_text_only():
    config = make_config(llm_model="some/model")
    text = build_configuration_text(config, StubRegistry([make_capability()]))
    assert "Free-text model: some/model" in text
    assert "Decision model: not configured" in text
    assert "lights_on: free-text model" in text


def test_reports_decision_model_without_extraction():
    config = make_config(
        structured_decision_model="typesafe/jev-1.13",
        routing_strategy="decision-select",
    )
    text = build_configuration_text(config, StubRegistry([_enum_capability()]))
    assert "Decision model: typesafe/jev-1.13" in text
    assert "Parameter extraction: off" in text
    assert "lights_on: decision model picks, free-text model fills" in text
    assert "lock_door: decision model only (no parameters)" in text


def test_reports_per_command_routing_with_extraction():
    config = make_config(
        structured_decision_model="typesafe/jev-1.13",
        routing_strategy="decision-extract",
        structured_decision_min_confidence=0.7,
    )
    text = build_configuration_text(config, StubRegistry([_enum_capability()]))
    assert "Parameter extraction: on (min confidence 0.7)" in text
    assert "lights_on: decision model only" in text
    assert "lock_door: decision model only (no parameters)" in text
    assert "remind: decision model picks, free-text model fills (when)" in text


def test_reports_no_services():
    config = make_config()
    text = build_configuration_text(config, StubRegistry([]))
    assert "no services with commands registered" in text


def test_api_key_value_is_never_shown():
    config = make_config(llm_api_key="sk-or-v1-supersecret")
    text = build_configuration_text(config, StubRegistry([make_capability()]))
    assert "supersecret" not in text
    assert "API key: set" in text


def test_configured_is_a_reserved_prefix():
    from telegram_proxy.commands import parse_explicit
    from telegram_proxy.config import RESERVED_PREFIXES
    from telegram_proxy.models import Capability

    assert parse_explicit("/configured").prefix in RESERVED_PREFIXES
    with pytest.raises(ValueError):
        Capability(service_id="svc", prefix="configured")


def test_decision_strategy_requires_decision_model():
    for strategy in ("decision-select", "decision-extract", "decision-only"):
        with pytest.raises(ConfigError):
            make_config(routing_strategy=strategy)


def test_unknown_strategy_rejected():
    with pytest.raises(ConfigError):
        make_config(routing_strategy="nonsense")


def test_min_confidence_must_be_a_probability():
    with pytest.raises(ConfigError):
        make_config(structured_decision_min_confidence=5.0)
    with pytest.raises(ConfigError):
        Config.from_env(
            {
                "TELEGRAM_BOT_TOKEN": "t",
                "LLM_API_KEY": "k",
                "STRUCTURED_DECISION_MIN_CONFIDENCE": "-1",
            }
        )
