from __future__ import annotations

import asyncio
import os
from datetime import datetime

from telegram_proxy_client import ServiceClient

client = ServiceClient(
    redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    service_id="time",
    prefix="time",
    display_name="Time",
    help=(
        "Reports the current date and time of the host running the service.\n"
        "Commands:\n"
        "  /time now — current date and time"
    ),
)


@client.command(
    "now",
    description="Tell the current date and time",
    examples=["what time is it", "what's the time", "tell me the time"],
    usage="now",
)
async def now(parameters: dict[str, str]) -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def main() -> None:
    await client.run()


if __name__ == "__main__":
    asyncio.run(main())
