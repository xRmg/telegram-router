from __future__ import annotations

import asyncio
import os

from telegram_proxy_client import ServiceClient

client = ServiceClient(
    redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    service_id="sniper-bot",
    prefix="sniper",
    display_name="Sniper",
)


async def main() -> None:
    await client.start()
    try:
        counter = 0
        while True:
            counter += 1
            await client.push(f"heartbeat {counter}: all systems nominal", level="info")
            await asyncio.sleep(60)
    finally:
        await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
