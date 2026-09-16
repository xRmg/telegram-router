from telegram_proxy.models import OutgoingMessage
from telegram_proxy.outgoing import OutgoingRelay
from telegram_proxy.ratelimit import FixedWindowLimiter
from telegram_proxy.registry import CapabilityRegistry
from tests.helpers import make_capability, make_config, put_capability


class FakeRouter:
    def __init__(self, resolves=True):
        self.resolves = resolves
        self.replies = []

    async def resolve_reply(self, message):
        self.replies.append(message)
        return self.resolves


async def make_relay(redis, notifier, config=None, resolves=True, window_seconds=60):
    config = config or make_config()
    registry = CapabilityRegistry(redis)
    await registry.refresh()
    relay = OutgoingRelay(
        redis,
        notifier,
        registry,
        config,
        FakeRouter(resolves=resolves),
        FixedWindowLimiter(redis, window_seconds=window_seconds),
    )
    return relay, registry


async def test_push_relayed_with_prefix(redis, notifier):
    await put_capability(redis, make_capability())
    relay, _ = await make_relay(redis, notifier)
    await relay.handle(
        OutgoingMessage(service_id="home-automation", text="Lights on").model_dump_json()
    )
    assert notifier.sent == [("HA: Lights on", "info")]


async def test_push_relayed_with_level_and_override(redis, notifier):
    await put_capability(redis, make_capability())
    config = make_config(service_display_names={"home-automation": "Home"})
    relay, _ = await make_relay(redis, notifier, config=config)
    await relay.handle(
        OutgoingMessage(
            service_id="home-automation", text="Alert", level="critical"
        ).model_dump_json()
    )
    assert notifier.sent == [("Home: Alert", "critical")]


async def test_push_unknown_service_uses_service_id(redis, notifier):
    relay, _ = await make_relay(redis, notifier)
    await relay.handle(OutgoingMessage(service_id="mystery", text="hi").model_dump_json())
    assert notifier.sent == [("mystery: hi", "info")]


async def test_reply_relayed(redis, notifier):
    await put_capability(redis, make_capability())
    relay, _ = await make_relay(redis, notifier)
    await relay.handle(
        OutgoingMessage(
            service_id="home-automation",
            request_id="r1",
            status="ok",
            text="done",
        ).model_dump_json()
    )
    assert notifier.sent == [("HA: done", "info")]


async def test_orphan_reply_dropped(redis, notifier):
    relay, _ = await make_relay(redis, notifier, resolves=False)
    await relay.handle(
        OutgoingMessage(
            service_id="home-automation",
            request_id="r1",
            status="ok",
            text="done",
        ).model_dump_json()
    )
    assert notifier.sent == []


async def test_invalid_message_rejected(redis, notifier):
    relay, _ = await make_relay(redis, notifier)
    await relay.handle('{"service_id": "x", "request_id": "r", "text": "missing status"}')
    await relay.handle("not json at all")
    assert notifier.sent == []


async def test_rate_limit_collapses_into_summary(redis, notifier):
    await put_capability(redis, make_capability())
    config = make_config(notifier_rate_limit_per_minute=1)
    relay, _ = await make_relay(redis, notifier, config=config, window_seconds=1)
    for _ in range(3):
        await relay.handle(
            OutgoingMessage(service_id="home-automation", text="ping").model_dump_json()
        )
    assert len(notifier.sent) == 1
    assert relay._overflow["home-automation"] == (2, "info")
    await asyncio_wait(1.5)
    assert len(notifier.sent) == 2
    assert "2 more notification(s) suppressed" in notifier.sent[1][0]
    assert relay._overflow == {}


async def test_summary_level_tracks_highest(redis, notifier):
    await put_capability(redis, make_capability())
    config = make_config(notifier_rate_limit_per_minute=1)
    relay, _ = await make_relay(redis, notifier, config=config, window_seconds=1)
    await relay.handle(OutgoingMessage(service_id="home-automation", text="a").model_dump_json())
    await relay.handle(
        OutgoingMessage(service_id="home-automation", text="b", level="warning").model_dump_json()
    )
    assert relay._overflow["home-automation"] == (1, "warning")
    await asyncio_wait(1.5)
    assert notifier.sent[1][1] == "warning"


async def test_rate_limit_is_per_service(redis, notifier):
    await put_capability(redis, make_capability())
    config = make_config(notifier_rate_limit_per_minute=1)
    relay, _ = await make_relay(redis, notifier, config=config)
    await relay.handle(OutgoingMessage(service_id="home-automation", text="a").model_dump_json())
    await relay.handle(OutgoingMessage(service_id="sniper-bot", text="b").model_dump_json())
    assert notifier.sent == [("HA: a", "info"), ("sniper-bot: b", "info")]


async def asyncio_wait(seconds):
    import asyncio

    await asyncio.sleep(seconds)
