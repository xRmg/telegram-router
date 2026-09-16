from __future__ import annotations

from .config import Config
from .models import Capability
from .registry import CapabilityRegistry, resolve_display_name


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
