import asyncio

from telegram_proxy.registry import CapabilityRegistry
from tests.helpers import make_capability, put_capability


async def _tick():
    await asyncio.sleep(0.05)


async def test_refresh_loads_capabilities(redis):
    await put_capability(redis, make_capability())
    registry = CapabilityRegistry(redis)
    await registry.refresh()
    assert registry.get("home-automation") is not None
    assert registry.by_prefix("ha").service_id == "home-automation"


async def test_refresh_with_no_keys(redis):
    registry = CapabilityRegistry(redis)
    await registry.refresh()
    assert registry.all() == []
    assert not registry.has_routable_commands()


async def test_invalid_payload_ignored(redis):
    await redis.set("capabilities:bad", "{not json")
    registry = CapabilityRegistry(redis)
    await registry.refresh()
    assert registry.get("bad") is None


async def test_mismatched_service_id_ignored(redis):
    await redis.set("capabilities:a", make_capability().model_dump_json())
    registry = CapabilityRegistry(redis)
    await registry.refresh()
    assert registry.get("a") is None


async def test_refresh_service_adds_and_drops(redis):
    registry = CapabilityRegistry(redis)
    await registry.refresh()
    await put_capability(redis, make_capability())
    await registry.refresh_service("home-automation")
    assert registry.get("home-automation") is not None
    await redis.delete("capabilities:home-automation")
    await registry.refresh_service("home-automation")
    assert registry.get("home-automation") is None
    assert registry.by_prefix("ha") is None


async def test_drop_service(redis):
    await put_capability(redis, make_capability())
    registry = CapabilityRegistry(redis)
    await registry.refresh()
    registry.drop_service("home-automation")
    assert registry.all() == []


async def test_prefix_conflict_first_wins(redis):
    await put_capability(redis, make_capability())
    await put_capability(redis, make_capability(service_id="other-service", commands=[]))
    registry = CapabilityRegistry(redis)
    await registry.refresh_service("home-automation")
    await registry.refresh_service("other-service")
    assert registry.by_prefix("ha").service_id == "home-automation"


async def test_is_live_reflects_key_existence(redis):
    registry = CapabilityRegistry(redis)
    await put_capability(redis, make_capability())
    assert await registry.is_live("home-automation")
    await redis.delete("capabilities:home-automation")
    assert not await registry.is_live("home-automation")


def test_by_ref_matches_prefix_service_id_and_display_name():
    registry = CapabilityRegistry(redis=None)
    capability = make_capability()
    registry._capabilities["home-automation"] = capability
    registry._rebuild_prefix_index()
    assert registry.by_ref("ha").service_id == "home-automation"
    assert registry.by_ref("HA").service_id == "home-automation"
    assert registry.by_ref("home-automation").service_id == "home-automation"
    assert registry.by_ref("Home Automation") is None
    assert registry.by_ref("nope") is None


async def test_changed_event_updates_cache(redis):
    await put_capability(redis, make_capability())
    registry = CapabilityRegistry(redis)
    await registry.start()
    try:
        await _tick()
        assert registry.get("home-automation") is not None
        updated = make_capability(display_name="Home")
        await redis.set("capabilities:home-automation", updated.model_dump_json())
        await redis.publish("capabilities:changed", "home-automation")
        await _tick()
        assert registry.get("home-automation").display_name == "Home"
    finally:
        await registry.stop()


async def test_expired_event_drops_cache(redis):
    await put_capability(redis, make_capability())
    registry = CapabilityRegistry(redis, db_index=0)
    await registry.start()
    try:
        await _tick()
        assert registry.get("home-automation") is not None
        await redis.publish("__keyevent@0__:expired", "capabilities:home-automation")
        await _tick()
        assert registry.get("home-automation") is None
    finally:
        await registry.stop()
