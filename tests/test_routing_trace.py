import pytest

from telegram_proxy.config import Config, ConfigError
from telegram_proxy.llm import RouteDecision, RouteOutcome, RouteStep
from telegram_proxy.router import Router, _verbose_trace
from tests.helpers import FakeLLM, FakeNotifier, make_capability, make_config, put_capability


def _decision():
    return RouteDecision(
        service_id="home-automation",
        command="lights_on",
        parameters={"room": "kitchen"},
        confidence=1.0,
        tool_name="home-automation.lights_on",
    )


def test_trace_lists_each_step_and_totals():
    outcome = RouteOutcome(
        decision=_decision(),
        steps=(
            RouteStep(stage="capability", model="typesafe/jev-1.13", seconds=0.45, cost=1.5e-05),
            RouteStep(stage="parameters", model="typesafe/jev-1.13", seconds=0.42, cost=1.7e-05),
        ),
    )
    text = _verbose_trace(outcome)
    assert "capability: typesafe/jev-1.13 (0.45s" in text
    assert "parameters: typesafe/jev-1.13 (0.42s" in text
    assert "total 0.87s" in text
    assert "$0.000032" in text
    assert "home-automation.lights_on" in text
    assert "confidence 1.00" in text


def test_trace_reports_no_match():
    outcome = RouteOutcome(
        decision=None,
        steps=(RouteStep(stage="capability", model="jev", seconds=0.4, cost=1e-05),),
    )
    assert "-> no match" in _verbose_trace(outcome)


def test_trace_without_cost_data():
    outcome = RouteOutcome(
        decision=_decision(),
        steps=(RouteStep(stage="chat", model="openrouter/auto", seconds=1.2),),
    )
    text = _verbose_trace(outcome)
    assert "chat: openrouter/auto (1.20s)" in text
    assert "total 1.20s" in text
    assert "$" not in text


def test_trace_without_steps():
    assert "no model calls" in _verbose_trace(RouteOutcome(decision=None))


def test_outcome_cost_is_none_when_unreported():
    outcome = RouteOutcome(
        decision=None, steps=(RouteStep(stage="chat", model="m", seconds=1.0),)
    )
    assert outcome.cost is None
    assert outcome.models == ["m"]


async def test_verbose_mode_sends_trace(redis):
    from telegram_proxy.ratelimit import FixedWindowLimiter
    from telegram_proxy.registry import CapabilityRegistry

    capability = make_capability()
    await put_capability(redis, capability)
    registry = CapabilityRegistry(redis)
    await registry.refresh()
    notifier = FakeNotifier()
    outcome = RouteOutcome(
        decision=_decision(),
        steps=(RouteStep(stage="capability", model="typesafe/jev-1.13", seconds=0.4, cost=1e-05),),
    )
    router = Router(
        redis=redis,
        registry=registry,
        config=make_config(routing_verbose=True),
        notifier=notifier,
        llm=FakeLLM(outcome=outcome),
        limiter=FixedWindowLimiter(redis),
    )

    await router.handle_text("turn on the lights in the kitchen")

    assert any("Routing trace:" in text for text, _ in notifier.sent)


async def test_verbose_disabled_sends_no_trace(redis):
    from telegram_proxy.ratelimit import FixedWindowLimiter
    from telegram_proxy.registry import CapabilityRegistry

    await put_capability(redis, make_capability())
    registry = CapabilityRegistry(redis)
    await registry.refresh()
    notifier = FakeNotifier()
    router = Router(
        redis=redis,
        registry=registry,
        config=make_config(),
        notifier=notifier,
        llm=FakeLLM(outcome=RouteOutcome(decision=_decision())),
        limiter=FixedWindowLimiter(redis),
    )

    await router.handle_text("turn on the lights in the kitchen")

    assert not any("Routing trace:" in text for text, _ in notifier.sent)


def test_routing_verbose_must_be_boolean():
    with pytest.raises(ConfigError):
        Config.from_env(
            {"TELEGRAM_BOT_TOKEN": "t", "LLM_API_KEY": "k", "ROUTING_VERBOSE": "maybe"}
        )
