from __future__ import annotations

from .config import Config
from .models import Capability, CommandSpec
from .registry import CapabilityRegistry, resolve_display_name


def build_configuration_text(config: Config, registry: CapabilityRegistry) -> str:
    lines = ["Model configuration:"]
    lines.append(f"  API key: {'set' if config.llm_api_key else 'not set'}")
    if not config.llm_enabled:
        lines.append("  Free-text routing: disabled (no LLM_API_KEY)")
        lines.append("")
        lines.append("Explicit commands (/<prefix> <command>) still work.")
        return "\n".join(lines)
    lines.append(f"  Routing strategy: {config.routing_strategy}")
    free_text = config.llm_model if config.chat_model_enabled else f"{config.llm_model} (unused)"
    lines.append(f"  Free-text model: {free_text}")
    if not config.structured_decision_model:
        lines.append("  Decision model: not configured")
    elif config.structured_decision_enabled:
        lines.append(f"  Decision model: {config.structured_decision_model}")
        if config.decision_extraction_enabled:
            lines.append(
                "  Parameter extraction: on "
                f"(min confidence {config.structured_decision_min_confidence:g})"
            )
        else:
            lines.append("  Parameter extraction: off")
    else:
        lines.append(f"  Decision model: {config.structured_decision_model} (unused)")
    lines.append(f"  Routing confidence threshold: {config.routing_confidence_threshold:g}")
    lines.append("")
    lines.append("Command routing:")
    services = [capability for capability in registry.all() if capability.commands]
    if not services:
        lines.append("  no services with commands registered")
    for capability in services:
        name = resolve_display_name(capability.service_id, config, registry)
        lines.append(f"  {name} (/{capability.prefix})")
        for command in capability.commands:
            lines.append(f"    {command.name}: {_command_routing(config, command)}")
    return "\n".join(lines)


def _command_routing(config: Config, command: CommandSpec) -> str:
    if not config.structured_decision_enabled:
        return "free-text model"
    if not command.parameters:
        return "decision model only (no parameters)"
    if config.decision_extraction_enabled and command.decision_routable:
        return "decision model only"
    blockers = ", ".join(
        name for name, spec in command.parameters.items() if not spec.decision_extractable
    )
    if not config.chat_model_enabled:
        return f"unavailable in free text — needs a free-text model ({blockers})"
    if not config.decision_extraction_enabled:
        return "decision model picks, free-text model fills"
    return f"decision model picks, free-text model fills ({blockers})"


def build_general_help(config: Config, registry: CapabilityRegistry) -> str:
    lines = [
        "This bot routes commands to registered services.",
        "",
        "Services:",
    ]
    services = registry.all()
    if not services:
        lines.append("  none registered")
    for capability in services:
        name = resolve_display_name(capability.service_id, config, registry)
        lines.append(f"  {name} (/{capability.prefix})")
        if not capability.commands:
            lines.append("    output only, no commands")
            continue
        for command in capability.commands:
            lines.append(f"    /{capability.prefix} {command.usage or command.name}")
    lines.append("")
    lines.append("Tap an example to run it, or type /<prefix> <command> key=value.")
    lines.append("/help <service> shows one service; /capabilities shows every detail.")
    lines.append("/configured shows which models handle routing.")
    return "\n".join(lines)


def build_service_help(config: Config, registry: CapabilityRegistry, service_ref: str) -> str:
    capability = registry.by_ref(service_ref)
    if capability is None:
        return f"Unknown service '{service_ref}'. Try /help to list services."
    return _render_service_help(config, registry, capability)


def _render_service_help(
    config: Config, registry: CapabilityRegistry, capability: Capability
) -> str:
    name = resolve_display_name(capability.service_id, config, registry)
    header = f"{name} (service_id={capability.service_id}, prefix=/{capability.prefix})"
    if capability.help:
        return f"{header}\n{capability.help}"
    lines = [header]
    if not capability.commands:
        lines.append("  output only, no commands")
        return "\n".join(lines)
    for command in capability.commands:
        suffix = " [confirm]" if command.confirm else ""
        lines.append(f"  /{capability.prefix} {command.usage or command.name}{suffix}")
        if command.description:
            lines.append(f"    {command.description}")
        for param_name, spec in command.parameters.items():
            required = "required" if spec.required else "optional"
            lines.append(f"    {param_name} ({spec.type}, {required}): {spec.description}")
    lines.append("")
    lines.append("Tap an example to run it.")
    return "\n".join(lines)
