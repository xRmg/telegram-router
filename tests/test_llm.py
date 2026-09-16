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


def make_router(client=None, capabilities=None):
    return ToolRouter(make_config(), StubRegistry(capabilities), client=client)


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
