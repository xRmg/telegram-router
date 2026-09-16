import pytest

from telegram_proxy.commands import CommandParseError, coerce_value, parse_command, parse_explicit
from telegram_proxy.registry import CapabilityRegistry, resolve_display_name
from tests.helpers import make_capability, make_config


def test_parse_explicit_slash():
    parsed = parse_explicit("/ha lights_on room=kitchen")
    assert parsed.prefix == "ha"
    assert parsed.rest == "lights_on room=kitchen"


def test_parse_explicit_bot_suffix_stripped():
    parsed = parse_explicit("/ha@MyBot lights_on")
    assert parsed.prefix == "ha"


def test_parse_explicit_at_prefix():
    parsed = parse_explicit("@home status")
    assert parsed.prefix == "home"


def test_parse_explicit_uppercase_normalized():
    parsed = parse_explicit("/HA lights_on")
    assert parsed.prefix == "ha"


def test_parse_explicit_free_text_is_none():
    assert parse_explicit("turn on the lights") is None


def test_parse_explicit_lone_slash_is_none():
    assert parse_explicit("/") is None


def test_parse_command_ok():
    capability = make_capability()
    command, parameters = parse_command("lights_on room=kitchen", capability)
    assert command.name == "lights_on"
    assert parameters == {"room": "kitchen"}


def test_parse_command_missing_name():
    with pytest.raises(CommandParseError):
        parse_command("", make_capability())


def test_parse_command_unknown_command():
    with pytest.raises(CommandParseError, match="unknown command"):
        parse_command("explode", make_capability())


def test_parse_command_unknown_parameter():
    with pytest.raises(CommandParseError, match="unknown parameter"):
        parse_command("lights_on room=x nope=y", make_capability())


def test_parse_command_missing_required():
    with pytest.raises(CommandParseError, match="missing required"):
        parse_command("lights_on", make_capability())


def test_parse_command_no_params_command():
    capability = make_capability(commands=[{"name": "status", "description": "Status"}])
    command, parameters = parse_command("status", capability)
    assert command.name == "status"
    assert parameters == {}


def test_parse_command_multi_word_value():
    capability = make_capability()
    command, parameters = parse_command("lights_on room=living room", capability)
    assert command.name == "lights_on"
    assert parameters == {"room": "living room"}


def test_parse_command_multiple_pairs_with_spaces():
    capability = make_capability(
        commands=[
            {
                "name": "set",
                "parameters": {
                    "room": {"type": "string", "required": True},
                    "note": {"type": "string", "required": True},
                },
            }
        ]
    )
    _, parameters = parse_command("set room=living room note=hello there", capability)
    assert parameters == {"room": "living room", "note": "hello there"}


def test_parse_command_bare_token_rejected():
    with pytest.raises(CommandParseError, match="key=value"):
        parse_command("lights_on kitchen", make_capability())


def test_coerce_value_types():
    capability = make_capability(
        commands=[
            {
                "name": "set",
                "parameters": {
                    "count": {"type": "number", "required": True},
                    "ratio": {"type": "number", "required": True},
                    "enabled": {"type": "boolean", "required": True},
                    "note": {"type": "string", "required": True},
                },
            }
        ]
    )
    command, parameters = parse_command("set count=3 ratio=1.5 enabled=yes note=hi", capability)
    assert parameters == {"count": 3, "ratio": 1.5, "enabled": True, "note": "hi"}


def test_coerce_boolean_variants():
    for raw, expected in [("true", True), ("off", False), ("0", False), ("on", True)]:
        assert coerce_value(raw, "boolean") is expected


def test_coerce_number_rejects_garbage():
    with pytest.raises(CommandParseError):
        coerce_value("abc", "number")


def test_validate_arguments_typed():
    from telegram_proxy.commands import validate_arguments

    capability = make_capability(
        commands=[
            {
                "name": "set",
                "parameters": {
                    "count": {"type": "number"},
                    "enabled": {"type": "boolean"},
                },
            }
        ]
    )
    command = capability.command_by_name("set")
    parameters = validate_arguments(command, {"count": "5", "enabled": True, "extra": 1})
    assert parameters == {"count": 5, "enabled": True}


def test_validate_arguments_missing_required():
    from telegram_proxy.commands import validate_arguments

    capability = make_capability()
    command = capability.command_by_name("lights_on")
    with pytest.raises(CommandParseError, match="missing required"):
        validate_arguments(command, {})


def test_resolve_display_name_override_wins():
    registry = CapabilityRegistry(redis=None)
    config = make_config(service_display_names={"home-automation": "Home"})
    assert resolve_display_name("home-automation", config, registry) == "Home"


def test_resolve_display_name_suggested_fallback():
    registry = CapabilityRegistry(redis=None)
    registry._capabilities["home-automation"] = make_capability()
    assert resolve_display_name("home-automation", make_config(), registry) == "HA"


def test_resolve_display_name_service_id_fallback():
    registry = CapabilityRegistry(redis=None)
    assert resolve_display_name("unknown-service", make_config(), registry) == "unknown-service"
