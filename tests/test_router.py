import asyncio
import json

from telegram_proxy.keys import capabilities_key, command_channel, pending_key
from telegram_proxy.llm import RouteDecision, RouteOutcome
from telegram_proxy.models import OutgoingMessage
from telegram_proxy.ratelimit import FixedWindowLimiter
from telegram_proxy.registry import CapabilityRegistry
from telegram_proxy.router import Router
from tests.helpers import FakeLLM, make_capability, make_config, put_capability


def capture_publish(redis):
    published = []

    async def fake_publish(channel, message):
        published.append((channel, message))
        return 0

    redis.publish = fake_publish
    return published


async def make_router(redis, notifier, config=None, llm=None, registry=None):
    config = config or make_config()
    registry = registry or CapabilityRegistry(redis)
    await registry.refresh()
    router = Router(
        redis,
        registry,
        config,
        notifier,
        llm or FakeLLM(),
        FixedWindowLimiter(redis),
    )
    return router, registry


def decision(confidence, parameters=None, command="lights_on"):
    return RouteDecision(
        service_id="home-automation",
        command=command,
        parameters=parameters or {"room": "kitchen"},
        confidence=confidence,
        tool_name="home-automation.lights_on",
    )


async def test_explicit_dispatch_publishes_command(redis, notifier):
    await put_capability(redis, make_capability())
    router, _ = await make_router(redis, notifier)
    published = capture_publish(redis)
    await router.handle_text("/ha lights_on room=kitchen")
    assert len(published) == 1
    channel, raw = published[0]
    assert channel == command_channel("home-automation")
    message = json.loads(raw)
    assert message["command"] == "lights_on"
    assert message["parameters"] == {"room": "kitchen"}
    assert message["request_id"]
    assert notifier.sent == []


async def test_explicit_unknown_prefix(redis, notifier):
    router, _ = await make_router(redis, notifier)
    capture_publish(redis)
    await router.handle_text("/nope do_it")
    assert notifier.sent == [("Unknown service '/nope'. Try /help.", "info")]


async def test_explicit_missing_required_parameter(redis, notifier):
    await put_capability(redis, make_capability())
    router, _ = await make_router(redis, notifier)
    capture_publish(redis)
    await router.handle_text("/ha lights_on")
    assert notifier.sent[0][0] == "HA: missing required parameter(s): room."


async def test_explicit_unknown_command(redis, notifier):
    await put_capability(redis, make_capability())
    router, _ = await make_router(redis, notifier)
    capture_publish(redis)
    await router.handle_text("/ha explode")
    assert "unknown command" in notifier.sent[0][0]


async def test_bare_prefix_runs_single_command(redis, notifier):
    capability = make_capability(
        service_id="time",
        prefix="time",
        display_name="Time",
        commands=[{"name": "now", "description": "Current time"}],
    )
    await put_capability(redis, capability)
    router, _ = await make_router(redis, notifier)
    published = capture_publish(redis)
    await router.handle_text("/time")
    assert len(published) == 1
    message = json.loads(published[0][1])
    assert message["command"] == "now"
    assert notifier.sent == []


async def test_bare_prefix_with_required_parameters_errors(redis, notifier):
    await put_capability(redis, make_capability())
    capability = make_capability(
        service_id="solo",
        prefix="solo",
        commands=[
            {
                "name": "run",
                "parameters": {"room": {"type": "string", "required": True}},
            }
        ],
    )
    await put_capability(redis, capability)
    router, _ = await make_router(redis, notifier)
    published = capture_publish(redis)
    await router.handle_text("/solo")
    assert published == []
    assert "missing required parameter(s): room" in notifier.sent[0][0]


async def test_bare_prefix_shows_service_help(redis, notifier):
    await put_capability(redis, make_capability())
    router, _ = await make_router(redis, notifier)
    published = capture_publish(redis)
    await router.handle_text("/ha")
    assert published == []
    assert "lights_on" in notifier.sent[0][0]
    assert "lock_door [confirm]" in notifier.sent[0][0]


async def test_bare_prefix_output_only_service_shows_help(redis, notifier):
    capability = make_capability(
        service_id="sniper-bot", prefix="sniper", display_name="Sniper", commands=[]
    )
    await put_capability(redis, capability)
    router, _ = await make_router(redis, notifier)
    published = capture_publish(redis)
    await router.handle_text("/sniper")
    assert published == []
    assert "output only" in notifier.sent[0][0]


async def test_dispatch_service_not_live(redis, notifier):
    await put_capability(redis, make_capability())
    router, _ = await make_router(redis, notifier)
    await redis.delete(capabilities_key("home-automation"))
    published = capture_publish(redis)
    await router.handle_text("/ha lights_on room=kitchen")
    assert published == []
    assert notifier.sent[0][0] == "HA is not available right now."


async def test_dispatch_unknown_service_id(redis, notifier):
    router, _ = await make_router(redis, notifier)
    published = capture_publish(redis)
    await router.handle_text("/ha lights_on room=kitchen")
    assert published == []
    assert notifier.sent[0][0] == "Unknown service '/ha'. Try /help."


async def test_free_text_dispatch(redis, notifier):
    await put_capability(redis, make_capability())
    llm = FakeLLM(RouteOutcome(decision=decision(0.9)))
    router, _ = await make_router(redis, notifier, llm=llm)
    published = capture_publish(redis)
    await router.handle_text("turn on the lights in the kitchen")
    assert len(published) == 1
    message = json.loads(published[0][1])
    assert message["command"] == "lights_on"
    assert message["parameters"] == {"room": "kitchen"}


async def test_free_text_low_confidence_blocked(redis, notifier):
    await put_capability(redis, make_capability())
    llm = FakeLLM(RouteOutcome(decision=decision(0.2)))
    router, _ = await make_router(redis, notifier, llm=llm)
    published = capture_publish(redis)
    await router.handle_text("something vague")
    assert published == []
    assert "not sure" in notifier.sent[0][0]


async def test_free_text_no_match(redis, notifier):
    await put_capability(redis, make_capability())
    llm = FakeLLM(RouteOutcome(decision=None))
    router, _ = await make_router(redis, notifier, llm=llm)
    published = capture_publish(redis)
    await router.handle_text("hello there")
    assert published == []
    assert "not sure" in notifier.sent[0][0]


async def test_free_text_llm_error(redis, notifier):
    await put_capability(redis, make_capability())
    llm = FakeLLM(error=RuntimeError("boom"))
    router, _ = await make_router(redis, notifier, llm=llm)
    published = capture_publish(redis)
    await router.handle_text("hello")
    assert published == []
    assert "not sure" in notifier.sent[0][0]


async def test_free_text_no_services(redis, notifier):
    router, _ = await make_router(redis, notifier)
    published = capture_publish(redis)
    await router.handle_text("hello")
    assert published == []
    assert "No services" in notifier.sent[0][0]


async def test_free_text_llm_disabled(redis, notifier):
    await put_capability(redis, make_capability())
    config = make_config(llm_api_key="")
    router, _ = await make_router(redis, notifier, config=config)
    published = capture_publish(redis)
    await router.handle_text("turn on the lights")
    assert published == []
    assert "not configured" in notifier.sent[0][0]


async def test_llm_rate_limit(redis, notifier):
    await put_capability(redis, make_capability())
    config = make_config(llm_rate_limit_per_minute=1)
    llm = FakeLLM(RouteOutcome(decision=decision(0.9)))
    router, _ = await make_router(redis, notifier, config=config, llm=llm)
    published = capture_publish(redis)
    await router.handle_text("first request")
    await router.handle_text("second request")
    assert len(published) == 1
    assert notifier.sent[-1][0].startswith("Too many requests")


async def test_confirmation_flow(redis, notifier):
    await put_capability(redis, make_capability())
    router, _ = await make_router(redis, notifier)
    published = capture_publish(redis)
    await router.handle_text("/ha lock_door")
    assert published == []
    assert len(notifier.confirmations) == 1
    _, confirmation_id = notifier.confirmations[0]
    assert await redis.exists(pending_key(confirmation_id)) == 1

    outcome = await router.handle_confirmation(confirmation_id, True)
    assert outcome == "dispatched"
    assert len(published) == 1
    message = json.loads(published[0][1])
    assert message["command"] == "lock_door"
    assert await redis.exists(pending_key(confirmation_id)) == 0


async def test_confirmation_no_discards(redis, notifier):
    await put_capability(redis, make_capability())
    router, _ = await make_router(redis, notifier)
    published = capture_publish(redis)
    await router.handle_text("/ha lock_door")
    _, confirmation_id = notifier.confirmations[0]
    outcome = await router.handle_confirmation(confirmation_id, False)
    assert outcome == "cancelled"
    assert published == []
    assert await redis.exists(pending_key(confirmation_id)) == 0


async def test_confirmation_unknown_id_expired(redis, notifier):
    await put_capability(redis, make_capability())
    router, _ = await make_router(redis, notifier)
    outcome = await router.handle_confirmation("does-not-exist", True)
    assert outcome == "expired"


async def test_confirmation_twice_expired(redis, notifier):
    await put_capability(redis, make_capability())
    router, _ = await make_router(redis, notifier)
    await router.handle_text("/ha lock_door")
    _, confirmation_id = notifier.confirmations[0]
    assert await router.handle_confirmation(confirmation_id, True) == "dispatched"
    assert await router.handle_confirmation(confirmation_id, True) == "expired"


async def test_reply_resolves_pending(redis, notifier):
    await put_capability(redis, make_capability())
    router, _ = await make_router(redis, notifier)
    published = capture_publish(redis)
    await router.handle_text("/ha lights_on room=kitchen")
    request_id = json.loads(published[0][1])["request_id"]
    reply = OutgoingMessage(
        service_id="home-automation",
        request_id=request_id,
        status="ok",
        text="Lights turned on in the kitchen",
    )
    assert await router.resolve_reply(reply) is True
    assert await router.resolve_reply(reply) is False


async def test_reply_after_timeout_sends_fallback(redis, notifier):
    await put_capability(redis, make_capability())
    config = make_config(reply_timeout_seconds=0.01)
    router, _ = await make_router(redis, notifier, config=config)
    published = capture_publish(redis)
    await router.handle_text("/ha lights_on room=kitchen")
    request_id = json.loads(published[0][1])["request_id"]
    await asyncio.sleep(0.05)
    assert notifier.sent[0][0] == "HA: no reply within 0.01s."
    reply = OutgoingMessage(
        service_id="home-automation",
        request_id=request_id,
        status="ok",
        text="late",
    )
    assert await router.resolve_reply(reply) is False
