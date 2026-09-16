from telegram_proxy.help_text import build_general_help, build_service_help
from telegram_proxy.registry import CapabilityRegistry
from tests.helpers import make_capability, make_config


def make_registry(*capabilities):
    registry = CapabilityRegistry(redis=None)
    for capability in capabilities:
        registry._capabilities[capability.service_id] = capability
    registry._rebuild_prefix_index()
    return registry


def test_general_help_lists_services_with_examples():
    registry = make_registry(
        make_capability(),
        make_capability(
            service_id="time",
            prefix="time",
            display_name="Time",
            commands=[
                {
                    "name": "now",
                    "description": "Current time",
                    "usage": "now",
                }
            ],
        ),
    )
    text = build_general_help(make_config(), registry)
    assert "routes commands to registered services" in text
    assert "HA (/ha)" in text
    assert "Time (/time)" in text
    assert "    /ha lights_on room=living room" in text
    assert "    /time now" in text
    assert "Tap an example to run it" in text


def test_general_help_without_services():
    text = build_general_help(make_config(), make_registry())
    assert "none registered" in text


def test_general_help_output_only_service():
    capability = make_capability(
        service_id="sniper-bot", prefix="sniper", display_name="Sniper", commands=[]
    )
    text = build_general_help(make_config(), make_registry(capability))
    assert "Sniper (/sniper)" in text
    assert "output only, no commands" in text


def test_service_help_uses_registered_content():
    capability = make_capability(help="Custom help body")
    text = build_service_help(make_config(), make_registry(capability), "ha")
    assert "Custom help body" in text
    assert "service_id=home-automation" in text


def test_service_help_shows_clickable_examples():
    text = build_service_help(make_config(), make_registry(make_capability()), "ha")
    assert "/ha lights_on room=living room" in text
    assert "/ha lock_door [confirm]" in text
    assert "Tap an example to run it" in text


def test_service_help_falls_back_to_command_name():
    capability = make_capability(
        commands=[{"name": "reboot", "description": "Reboot", "usage": None}]
    )
    text = build_service_help(make_config(), make_registry(capability), "ha")
    assert "/ha reboot" in text


def test_service_help_output_only_service():
    capability = make_capability(service_id="sniper-bot", prefix="sniper", commands=[], help=None)
    text = build_service_help(make_config(), make_registry(capability), "sniper")
    assert "output only" in text


def test_service_help_unknown_service():
    text = build_service_help(make_config(), make_registry(), "nope")
    assert text == "Unknown service 'nope'. Try /help to list services."
