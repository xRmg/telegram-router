import json

from telegram_proxy.keys import CAPABILITIES_CHANGED_CHANNEL, OUTGOING_CHANNEL, capabilities_key
from telegram_proxy_client import Parameter, ServiceClient


def make_client(redis, **overrides):
    values = {
        "service_id": "home-automation",
        "prefix": "ha",
        "display_name": "HA",
        "redis": redis,
    }
    values.update(overrides)
    return ServiceClient(**values)


def capture_publish(redis):
    published = []

    async def fake_publish(channel, message):
        published.append((channel, message))
        return 0

    redis.publish = fake_publish
    return published


async def test_capability_payload_shape(redis):
    client = make_client(redis)

    @client.command(
        "lights_on",
        description="Turn on the lights",
        examples=["turn on the lights"],
        usage="lights_on room=living room",
        parameters={"room": Parameter(type="string", description="Room", required=True)},
    )
    async def lights_on(parameters):
        return f"on {parameters['room']}"

    payload = client.capability_payload()
    assert payload["schema_version"] == 1
    assert payload["service_id"] == "home-automation"
    assert payload["prefix"] == "ha"
    assert payload["display_name"] == "HA"
    assert payload["help"] is None
    command = payload["commands"][0]
    assert command["name"] == "lights_on"
    assert command["examples"] == ["turn on the lights"]
    assert command["usage"] == "lights_on room=living room"
    assert command["parameters"]["room"] == {
        "type": "string",
        "description": "Room",
        "required": True,
    }


async def test_capability_payload_includes_help(redis):
    client = make_client(redis, help="Some help")
    assert client.capability_payload()["help"] == "Some help"


async def test_register_sets_key_with_ttl_and_publishes(redis):
    client = make_client(redis)
    published = capture_publish(redis)
    await client._register()
    assert await redis.exists(capabilities_key("home-automation")) == 1
    ttl = await redis.ttl(capabilities_key("home-automation"))
    assert 88 <= ttl <= 90
    assert published == [
        (CAPABILITIES_CHANGED_CHANNEL, "home-automation"),
    ]


async def test_handle_command_ok_reply(redis):
    client = make_client(redis)

    @client.command("lights_on")
    async def lights_on(parameters):
        return f"on {parameters['room']}"

    published = capture_publish(redis)
    await client._handle_command(
        json.dumps({"request_id": "a1b2c3", "command": "lights_on", "parameters": {"room": "x"}})
    )
    channel, raw = published[0]
    assert channel == OUTGOING_CHANNEL
    reply = json.loads(raw)
    assert reply == {
        "service_id": "home-automation",
        "request_id": "a1b2c3",
        "status": "ok",
        "text": "on x",
    }


async def test_handle_command_error_reply(redis):
    client = make_client(redis)

    @client.command("boom")
    async def boom(parameters):
        raise RuntimeError("kaboom")

    published = capture_publish(redis)
    await client._handle_command(
        json.dumps({"request_id": "r", "command": "boom", "parameters": {}})
    )
    reply = json.loads(published[0][1])
    assert reply["status"] == "error"
    assert "kaboom" in reply["text"]


async def test_handle_unknown_command(redis):
    client = make_client(redis)
    published = capture_publish(redis)
    await client._handle_command(
        json.dumps({"request_id": "r", "command": "nope", "parameters": {}})
    )
    reply = json.loads(published[0][1])
    assert reply["status"] == "error"
    assert "unknown command" in reply["text"]


async def test_handle_bad_payload_ignored(redis):
    client = make_client(redis)
    published = capture_publish(redis)
    await client._handle_command("not json")
    assert published == []


async def test_push_publishes_without_request_id(redis):
    client = make_client(redis)
    published = capture_publish(redis)
    await client.push("Order filled", level="warning")
    channel, raw = published[0]
    assert channel == OUTGOING_CHANNEL
    message = json.loads(raw)
    assert message == {
        "service_id": "home-automation",
        "text": "Order filled",
        "level": "warning",
    }
