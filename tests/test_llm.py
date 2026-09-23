from types import SimpleNamespace

from telegram_proxy.llm import ToolRouter
from tests.helpers import make_capability, make_config


class StubRegistry:
    def __init__(self, capabilities=None):
        self._capabilities = list(capabilities or [])

    def all(self):
        return list(self._capabilities)


class FakeCompletionsClient:
    def __init__(self, tool_calls=None):
        message = SimpleNamespace(
            tool_calls=tool_calls,
            content=None,
            model_dump_json=lambda: '{"stub": true}',
        )
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        self.captured = None

        async def create(**kwargs):
            self.captured = kwargs
            return response

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def tool_call(name, arguments):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeDecisionsClient:
    def __init__(self, choice, confidence=1.0, params=None):
        self._choice = choice
        self._confidence = confidence
        self._params = params or {}
        self.captured = []

    @property
    def calls(self) -> int:
        return len(self.captured)

    async def post(self, path, json=None):
        self.captured.append((path, json))
        questions = json["questions"]
        if "capability" in questions:
            answers = {
                "capability": {
                    "type": "choice",
                    "choice": self._choice,
                    "confidence": self._confidence,
                }
            }
        else:
            answers = {
                name: {
                    "type": "choice",
                    "choice": self._params[name][0],
                    "confidence": self._params[name][1],
                }
                for name in questions
            }
        return _Response({"answers": answers, "usage": {"cost": 0.0}})


def make_router(client=None, capabilities=None, config=None, decisions_client=None):
    return ToolRouter(
        config or make_config(),
        StubRegistry(capabilities),
        client=client,
        decisions_client=decisions_client,
    )


def test_build_tools_namespaced_and_confidence_required():
    router = make_router(capabilities=[make_capability()])
    tools, index = router.build_tools()
    names = {tool["function"]["name"] for tool in tools}
    assert names == {
        "home-automation.lights_on",
        "home-automation.lights_off",
        "home-automation.lock_door",
    }
    by_name = {tool["function"]["name"]: tool for tool in tools}
    lights_on = by_name["home-automation.lights_on"]
    parameters = lights_on["function"]["parameters"]
    assert parameters["properties"]["confidence"]["type"] == "number"
    assert "confidence" in parameters["required"]
    assert "room" in parameters["required"]
    assert "Examples: turn on the lights" in lights_on["function"]["description"]
    assert index["home-automation.lights_on"][0] == "home-automation"


def test_build_tools_empty_registry():
    router = make_router()
    tools, index = router.build_tools()
    assert tools == []
    assert index == {}


async def test_route_success():
    client = FakeCompletionsClient(
        tool_calls=[
            tool_call("home-automation.lights_on", '{"room": "kitchen", "confidence": 0.9}')
        ]
    )
    router = make_router(client=client, capabilities=[make_capability()])
    outcome = await router.route("turn on the lights in the kitchen")
    decision = outcome.decision
    assert decision is not None
    assert decision.service_id == "home-automation"
    assert decision.command == "lights_on"
    assert decision.parameters == {"room": "kitchen"}
    assert decision.confidence == 0.9
    assert decision.tool_name == "home-automation.lights_on"
    assert outcome.raw_response
    assert client.captured["model"] == "openrouter/auto"
    assert client.captured["temperature"] == 0


async def test_route_no_tool_call():
    client = FakeCompletionsClient(tool_calls=[])
    router = make_router(client=client, capabilities=[make_capability()])
    outcome = await router.route("hello")
    assert outcome.decision is None
    assert outcome.error is None


async def test_route_missing_confidence_is_zero():
    client = FakeCompletionsClient(
        tool_calls=[tool_call("home-automation.lights_on", '{"room": "kitchen"}')]
    )
    router = make_router(client=client, capabilities=[make_capability()])
    outcome = await router.route("lights")
    assert outcome.decision.confidence == 0.0


async def test_route_confidence_clamped():
    client = FakeCompletionsClient(
        tool_calls=[tool_call("home-automation.lights_on", '{"room": "kitchen", "confidence": 3}')]
    )
    router = make_router(client=client, capabilities=[make_capability()])
    outcome = await router.route("lights")
    assert outcome.decision.confidence == 1.0


async def test_route_unknown_tool():
    client = FakeCompletionsClient(tool_calls=[tool_call("other.thing", "{}")])
    router = make_router(client=client, capabilities=[make_capability()])
    outcome = await router.route("anything")
    assert outcome.decision is None
    assert outcome.error == "unknown_tool"


async def test_route_invalid_arguments_json():
    client = FakeCompletionsClient(tool_calls=[tool_call("home-automation.lights_on", "not json")])
    router = make_router(client=client, capabilities=[make_capability()])
    outcome = await router.route("lights")
    assert outcome.decision is None
    assert outcome.error == "invalid_arguments"


async def test_route_missing_required_parameter():
    client = FakeCompletionsClient(
        tool_calls=[tool_call("home-automation.lights_on", '{"confidence": 0.9}')]
    )
    router = make_router(client=client, capabilities=[make_capability()])
    outcome = await router.route("lights")
    assert outcome.decision is None
    assert outcome.error == "invalid_arguments"


async def test_route_empty_registry_skips_client():
    router = make_router(capabilities=[])
    outcome = await router.route("anything")
    assert outcome.decision is None
    assert outcome.error == "no_tools"


def _decision_config(**overrides):
    values = {
        "structured_decision_model": "typesafe/jev-1.13",
        "routing_strategy": "decision-select",
    }
    values.update(overrides)
    return make_config(**values)


async def test_route_decision_model_selects_and_extracts():
    decisions = FakeDecisionsClient(choice="home-automation.lights_on", confidence=0.87)
    chat = FakeCompletionsClient(
        tool_calls=[tool_call("home-automation.lights_on", '{"room": "kitchen"}')]
    )
    router = make_router(
        client=chat,
        decisions_client=decisions,
        capabilities=[make_capability()],
        config=_decision_config(),
    )
    outcome = await router.route("turn on the lights in the kitchen")
    decision = outcome.decision
    assert decision is not None
    assert decision.service_id == "home-automation"
    assert decision.command == "lights_on"
    assert decision.parameters == {"room": "kitchen"}
    assert decision.confidence == 0.87
    path, payload = decisions.captured[0]
    assert path == "/decisions"
    assert payload["model"] == "typesafe/jev-1.13"
    assert payload["state"] == {"message": "turn on the lights in the kitchen"}
    criteria = payload["questions"]["capability"]["criteria"]
    assert "none" in criteria
    assert "home-automation.lights_on" in criteria
    forced_tools = chat.captured["tools"]
    assert len(forced_tools) == 1
    assert forced_tools[0]["function"]["name"] == "home-automation.lights_on"
    assert chat.captured["tool_choice"] == {
        "type": "function",
        "function": {"name": "home-automation.lights_on"},
    }


async def test_route_decision_model_none_skips_chat_call():
    decisions = FakeDecisionsClient(choice="none")
    chat = FakeCompletionsClient()
    router = make_router(
        client=chat,
        decisions_client=decisions,
        capabilities=[make_capability()],
        config=_decision_config(),
    )
    outcome = await router.route("what's the weather")
    assert outcome.decision is None
    assert chat.captured is None


async def test_route_decision_model_no_parameters_skips_chat_call():
    decisions = FakeDecisionsClient(choice="home-automation.lock_door", confidence=0.95)
    chat = FakeCompletionsClient()
    router = make_router(
        client=chat,
        decisions_client=decisions,
        capabilities=[make_capability()],
        config=_decision_config(),
    )
    outcome = await router.route("lock the door")
    decision = outcome.decision
    assert decision is not None
    assert decision.service_id == "home-automation"
    assert decision.command == "lock_door"
    assert decision.parameters == {}
    assert decision.confidence == 0.95
    assert chat.captured is None


def _extraction_config(**overrides):
    return _decision_config(routing_strategy="decision-extract", **overrides)


def _enum_capability():
    return make_capability(
        commands=[
            {
                "name": "lights_on",
                "description": "Turn on the lights in a room",
                "parameters": {
                    "room": {
                        "type": "string",
                        "description": "Room name",
                        "required": True,
                        "enum": ["kitchen", "attic"],
                    }
                },
            }
        ]
    )


def _span_capability():
    return make_capability(
        commands=[
            {
                "name": "lights_on",
                "description": "Turn on the lights in a room",
                "parameters": {
                    "room": {
                        "type": "string",
                        "description": "Room name",
                        "required": True,
                        "extract": "span",
                    }
                },
            }
        ]
    )


async def test_extraction_enum_fills_parameters_without_chat_call():
    decisions = FakeDecisionsClient(
        choice="home-automation.lights_on",
        confidence=0.9,
        params={"room": ("kitchen", 1.0)},
    )
    chat = FakeCompletionsClient()
    router = make_router(
        client=chat,
        decisions_client=decisions,
        capabilities=[_enum_capability()],
        config=_extraction_config(),
    )
    outcome = await router.route("turn on the lights in the kitchen")
    assert outcome.decision is not None
    assert outcome.decision.parameters == {"room": "kitchen"}
    assert outcome.decision.confidence == 0.9
    assert chat.captured is None
    assert decisions.calls == 2
    _, payload = decisions.captured[1]
    assert set(payload["questions"]["room"]["criteria"]) == {"kitchen", "attic", "__absent__"}


async def test_extraction_span_fills_parameters_without_chat_call():
    decisions = FakeDecisionsClient(
        choice="home-automation.lights_on",
        confidence=0.8,
        params={"room": ("the kitchen", 0.9)},
    )
    chat = FakeCompletionsClient()
    router = make_router(
        client=chat,
        decisions_client=decisions,
        capabilities=[_span_capability()],
        config=_extraction_config(),
    )
    outcome = await router.route("turn on the lights in the kitchen")
    assert outcome.decision.parameters == {"room": "the kitchen"}
    assert chat.captured is None


async def test_extraction_absent_required_parameter_is_invalid():
    decisions = FakeDecisionsClient(
        choice="home-automation.lights_on",
        confidence=0.9,
        params={"room": ("__absent__", 1.0)},
    )
    chat = FakeCompletionsClient()
    router = make_router(
        client=chat,
        decisions_client=decisions,
        capabilities=[_enum_capability()],
        config=_extraction_config(),
    )
    outcome = await router.route("turn on the lights")
    assert outcome.decision is None
    assert outcome.error == "invalid_arguments"
    assert chat.captured is None


async def test_extraction_low_confidence_falls_back_to_chat():
    decisions = FakeDecisionsClient(
        choice="home-automation.lights_on",
        confidence=0.9,
        params={"room": ("kitchen", 0.2)},
    )
    chat = FakeCompletionsClient(
        tool_calls=[tool_call("home-automation.lights_on", '{"room": "kitchen"}')]
    )
    router = make_router(
        client=chat,
        decisions_client=decisions,
        capabilities=[_enum_capability()],
        config=_extraction_config(),
    )
    outcome = await router.route("turn on the lights in the kitchen")
    assert outcome.decision.parameters == {"room": "kitchen"}
    assert outcome.decision.confidence == 0.9
    assert chat.captured is not None


async def test_extraction_unknown_option_falls_back_to_chat():
    decisions = FakeDecisionsClient(
        choice="home-automation.lights_on",
        confidence=0.9,
        params={"room": ("basement", 1.0)},
    )
    chat = FakeCompletionsClient(
        tool_calls=[tool_call("home-automation.lights_on", '{"room": "kitchen"}')]
    )
    router = make_router(
        client=chat,
        decisions_client=decisions,
        capabilities=[_enum_capability()],
        config=_extraction_config(),
    )
    outcome = await router.route("lights on in the basement")
    assert chat.captured is not None
    assert outcome.decision.parameters == {"room": "kitchen"}


async def test_extraction_skipped_for_non_routable_command():
    decisions = FakeDecisionsClient(choice="home-automation.lights_on", confidence=0.9)
    chat = FakeCompletionsClient(
        tool_calls=[tool_call("home-automation.lights_on", '{"room": "kitchen"}')]
    )
    router = make_router(
        client=chat,
        decisions_client=decisions,
        capabilities=[make_capability()],
        config=_extraction_config(),
    )
    outcome = await router.route("turn on the lights in the kitchen")
    assert outcome.decision.parameters == {"room": "kitchen"}
    assert decisions.calls == 1
    assert chat.captured is not None


async def test_extraction_disabled_uses_chat_model():
    decisions = FakeDecisionsClient(choice="home-automation.lights_on", confidence=0.9)
    chat = FakeCompletionsClient(
        tool_calls=[tool_call("home-automation.lights_on", '{"room": "kitchen"}')]
    )
    router = make_router(
        client=chat,
        decisions_client=decisions,
        capabilities=[_enum_capability()],
        config=_decision_config(),
    )
    await router.route("turn on the lights in the kitchen")
    assert decisions.calls == 1
    assert chat.captured is not None


async def test_route_decision_model_unknown_choice_skips_chat_call():
    decisions = FakeDecisionsClient(choice="not-a-real-tool")
    chat = FakeCompletionsClient()
    router = make_router(
        client=chat,
        decisions_client=decisions,
        capabilities=[make_capability()],
        config=_decision_config(),
    )
    outcome = await router.route("anything")
    assert outcome.decision is None
    assert chat.captured is None
