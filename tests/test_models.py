import pytest
from pydantic import ValidationError

from telegram_proxy.models import CommandMessage, OutgoingMessage
from tests.helpers import make_capability


def test_valid_capability():
    capability = make_capability()
    assert capability.command_by_name("LIGHTS_ON").name == "lights_on"
    assert capability.command_by_name("missing") is None


def test_unsupported_schema_version_rejected():
    with pytest.raises(ValidationError):
        make_capability(schema_version=2)


def test_reserved_prefix_rejected():
    with pytest.raises(ValidationError):
        make_capability(prefix="help")


def test_uppercase_prefix_rejected():
    with pytest.raises(ValidationError):
        make_capability(prefix="HA")


def test_reserved_parameter_name_rejected():
    with pytest.raises(ValidationError):
        make_capability(
            commands=[
                {
                    "name": "cmd",
                    "parameters": {
                        "confidence": {"type": "number", "description": "x", "required": False}
                    },
                }
            ]
        )


def test_duplicate_commands_rejected():
    with pytest.raises(ValidationError):
        make_capability(commands=[{"name": "a"}, {"name": "a"}])


def test_empty_service_id_rejected():
    with pytest.raises(ValidationError):
        make_capability(service_id="  ")


def test_capability_without_commands_valid():
    capability = make_capability(commands=[])
    assert capability.commands == []


def test_reply_requires_status():
    with pytest.raises(ValidationError):
        OutgoingMessage.model_validate({"service_id": "x", "request_id": "r", "text": "hi"})


def test_push_with_status_rejected():
    with pytest.raises(ValidationError):
        OutgoingMessage.model_validate({"service_id": "x", "text": "hi", "status": "ok"})


def test_push_defaults():
    message = OutgoingMessage.model_validate({"service_id": "x", "text": "hi"})
    assert message.request_id is None
    assert message.status is None
    assert message.level == "info"


def test_reply_level_default():
    message = OutgoingMessage.model_validate(
        {"service_id": "x", "request_id": "r", "status": "error", "text": "boom"}
    )
    assert message.level == "info"
    assert message.status == "error"


def test_command_message_round_trip():
    message = CommandMessage(request_id="a1b2c3", command="lights_on", parameters={"room": "x"})
    parsed = CommandMessage.model_validate_json(message.model_dump_json())
    assert parsed == message
