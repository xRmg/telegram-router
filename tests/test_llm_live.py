"""Live smoke test against real OpenRouter endpoints.

Opt-in only: requires OPENROUTER_TEST_API_KEY. Unlike the rest of the suite,
this makes real, billed network calls (the Decisions API for capability
selection, then chat completions for parameter extraction), so it's skipped
unless that key is set and is never run automatically in CI.
"""

from __future__ import annotations

import os

import pytest

from telegram_proxy.llm import ToolRouter
from tests.helpers import make_capability, make_config

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_TEST_API_KEY"),
    reason="set OPENROUTER_TEST_API_KEY to run the live OpenRouter smoke test",
)


class _StaticRegistry:
    def __init__(self, capabilities):
        self._capabilities = list(capabilities)

    def all(self):
        return list(self._capabilities)


async def test_route_live_structured_decision_capability_selection():
    api_key = os.environ["OPENROUTER_TEST_API_KEY"]
    decision_model = os.environ.get("OPENROUTER_TEST_DECISION_MODEL", "typesafe/jev-1.13")
    config = make_config(
        llm_api_key=api_key,
        structured_decision_model=decision_model,
        routing_strategy="decision-select",
    )
    router = ToolRouter(config, _StaticRegistry([make_capability()]))

    outcome = await router.route("turn on the lights in the kitchen")

    assert outcome.error is None, outcome.raw_response
    assert outcome.decision is not None, outcome.raw_response
    assert outcome.decision.service_id == "home-automation"
    assert outcome.decision.command == "lights_on"
    assert outcome.decision.parameters.get("room") == "kitchen"
    assert outcome.decision.confidence > 0


async def test_route_live_enum_extraction_skips_chat_model():
    api_key = os.environ["OPENROUTER_TEST_API_KEY"]
    decision_model = os.environ.get("OPENROUTER_TEST_DECISION_MODEL", "typesafe/jev-1.13")
    config = make_config(
        llm_api_key=api_key,
        llm_model="unusable/model-that-must-not-be-called",
        structured_decision_model=decision_model,
        routing_strategy="decision-extract",
    )
    capability = make_capability(
        commands=[
            {
                "name": "lights_on",
                "description": "Turn on the lights in a room",
                "examples": ["turn on the lights in the living room"],
                "parameters": {
                    "room": {
                        "type": "string",
                        "description": "Room name",
                        "required": True,
                        "enum": ["living room", "kitchen", "attic"],
                    }
                },
            }
        ]
    )
    router = ToolRouter(config, _StaticRegistry([capability]))

    outcome = await router.route("turn on the lights in the kitchen")

    assert outcome.error is None, outcome.raw_response
    assert outcome.decision is not None, outcome.raw_response
    assert outcome.decision.command == "lights_on"
    assert outcome.decision.parameters == {"room": "kitchen"}


async def test_route_live_structured_decision_no_match():
    api_key = os.environ["OPENROUTER_TEST_API_KEY"]
    decision_model = os.environ.get("OPENROUTER_TEST_DECISION_MODEL", "typesafe/jev-1.13")
    config = make_config(
        llm_api_key=api_key,
        structured_decision_model=decision_model,
        routing_strategy="decision-select",
    )
    router = ToolRouter(config, _StaticRegistry([make_capability()]))

    outcome = await router.route("what's the weather like in Amsterdam today")

    assert outcome.decision is None, outcome.raw_response
