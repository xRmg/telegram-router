from __future__ import annotations

import asyncio
import os

from telegram_proxy_client import Parameter, ServiceClient

client = ServiceClient(
    redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    service_id="home-automation",
    prefix="ha",
    display_name="HA",
    help=(
        "Simulated home automation.\n"
        "Commands:\n"
        "  /ha lights_on room=<name> — turn on the lights in a room\n"
        "  /ha lights_off room=<name> — turn off the lights in a room\n"
        "  /ha status — show which rooms have their lights on\n"
        "  /ha lock_door — lock the front door (asks for confirmation)"
    ),
)

ROOMS = ["living room", "kitchen", "bedroom", "bathroom", "garage", "attic"]

lights: dict[str, bool] = {}


@client.command(
    "lights_on",
    description="Turn on the lights in a room",
    examples=["turn on the lights in the living room", "lights on in the kitchen"],
    usage="lights_on room=living room",
    parameters={
        "room": Parameter(
            type="string", description="Room name", required=True, enum=ROOMS
        )
    },
)
async def lights_on(parameters: dict[str, str]) -> str:
    room = parameters["room"]
    lights[room] = True
    return f"Lights turned on in the {room}"


@client.command(
    "lights_off",
    description="Turn off the lights in a room",
    examples=["turn off the lights"],
    usage="lights_off room=living room",
    parameters={
        "room": Parameter(
            type="string", description="Room name", required=True, enum=ROOMS
        )
    },
)
async def lights_off(parameters: dict[str, str]) -> str:
    room = parameters["room"]
    lights[room] = False
    return f"Lights turned off in the {room}"


@client.command(
    "lock_door",
    description="Lock the front door",
    examples=["lock the door"],
    usage="lock_door",
    confirm=True,
)
async def lock_door(parameters: dict[str, str]) -> str:
    return "Front door locked"


@client.command(
    "status",
    description="Report which rooms have their lights on",
    examples=["what is on", "status"],
    usage="status",
)
async def status(parameters: dict[str, str]) -> str:
    on = [room for room, state in lights.items() if state]
    return "Lights on: " + (", ".join(on) if on else "none")


async def main() -> None:
    await client.run()


if __name__ == "__main__":
    asyncio.run(main())
