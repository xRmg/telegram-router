# Telegram Command Proxy

One dockerized service that holds the only connection to a Telegram bot. Client
services register their commands with the proxy instead of talking to Telegram
themselves, and a user reaches them through explicit commands or free text.

## How it works

```mermaid
flowchart TD
    U["User (owner chat)"] -->|message| B["Telegram bot"]
    B <-->|"long polling"| P["Proxy / router"]

    subgraph Routing paths
        P -->|"1. explicit /prefix command"| LIVE{"service live<br/>in registry?"}
        LIVE -->|no| UNAVAIL["reply: service unavailable"]
        LIVE -->|yes| CONFIRM{"confirm flag?"}
        CONFIRM -->|yes| PEND["store pending:&lt;id&gt;<br/>Yes/No buttons"]
        PEND -->|Yes| PUB
        CONFIRM -->|no| PUB["publish cmd:&lt;service_id&gt;"]
        P -->|"2. free text"| LLM["LLM tool call<br/>+ confidence score"]
        LLM -->|below threshold| UNSURE["reply: not sure"]
        LLM -->|at or above threshold| CONFIRM
    end

    subgraph Redis
        REG["capabilities:&lt;service_id&gt;<br/>TTL 90s, heartbeat 30s"]
        CH["channels and keys"]
    end

    PUB --> CH
    CH -->|"subscribe cmd:&lt;service_id&gt;"| CLIENTS["Client services"]
    REG <-->|"register and refresh"| CLIENTS
    CLIENTS -->|"reply / unsolicited push"| CH
    CH -->|"telegram:outgoing"| RELAY["Outgoing relay<br/>display-name prefix, rate limit"]
    RELAY --> B
    UNAVAIL --> B
    UNSURE --> B
    PEND --> B
```

The proxy is the only connection to Telegram. Client services register their
capabilities in Redis and receive commands over pub/sub; every reply and
unsolicited push flows back through the proxy to the single owner chat.

## Quick start

Create a Telegram bot with [@BotFather](https://t.me/BotFather), run `/newbot`,
and copy the API token it returns.

```bash
cp .env.example .env
# fill in TELEGRAM_BOT_TOKEN, then start:
docker compose --profile demo up --build
```

Leave `TELEGRAM_OWNER_CHAT_ID` empty on first run. The proxy starts in
learn-owner mode and prints a one-time token in logs/stdout. Send that exact
token to your bot in Telegram; it replies with your chat id, stores it in
Redis, and becomes active immediately (no restart needed).

If your Redis data persists, this learned owner is reused on restart (no
re-pairing). Set `TELEGRAM_OWNER_CHAT_ID` in `.env` when you want to bind the
bot to an existing owner chat without learn mode, or when you need startup to
work after Redis is reset/new:

```bash
docker compose up -d proxy
```

`LLM_API_KEY` is optional in v0.1: free-text routing replies "not configured"
until an OpenAI-compatible key is provided (`LLM_BASE_URL`/`LLM_MODEL` can point
at any OpenAI-compatible endpoint).

## Usage

| Message | Effect |
|---|---|
| `/help` | Router usage and list of services |
| `/help <service>` | One service's help text |
| `/capabilities` | Every registered capability in detail |
| `/ha lights_on room=living room` | Explicit command (parameters as `key=value`) |
| `/time` | Runs the service's only command |
| plain text | LLM-routed free text (needs `LLM_API_KEY`) |

Help text shows complete, tappable examples like `/time now` or
`/ha lights_on room=living room`.

## Demo services

`docker compose --profile demo up` additionally runs two example clients:

- `examples/time_service.py` — `/time now`; a minimal command service.
- `examples/home_automation.py` — `/ha`; commands, parameters, and a
  confirmation-gated command (`/ha lock_door`).
- `examples/alert_pusher.py` — an output-only service that pushes alerts
  without a `request_id`.

## Client SDK

```python
import asyncio

from telegram_proxy_client import Parameter, ServiceClient

client = ServiceClient(
    redis_url="redis://redis:6379/0",
    service_id="weather",
    prefix="wx",
    display_name="Wx",
    help="Weather reports.\nCommands:\n  /wx now — current conditions",
)


@client.command(
    "now",
    description="Current weather",
    examples=["what's the weather like"],
    usage="now",
    parameters={"city": Parameter(type="string", required=True)},
)
async def now(parameters: dict[str, str]) -> str:
    return f"Weather in {parameters['city']}: sunny, 21C"


async def main() -> None:
    await client.run()


if __name__ == "__main__":
    asyncio.run(main())
```

The client registers its capabilities on Redis with a heartbeat (30s refresh,
90s TTL), listens on `cmd:<service_id>` for commands, and publishes replies to
`telegram:outgoing`. Output-only services call `await client.push(text, level=...)`
and register no commands.

## Configuration

See `.env.example` for every variable. Notable: Redis must run with
`notify-keyspace-events Ex` (the compose file already does) so expired
capabilities drop out of the router's cache.

## Redis security

Redis runs with ACLs. By default nothing is enforced (dev mode); to enable
authentication, set `REDIS_PASSWORD` in `.env` and give every client service
its own password:

| Variable | User | Access |
|---|---|---|
| `REDIS_PASSWORD` | `proxy` | all capability/pending/ratelimit keys, all channels, subscribe/publish |
| `REDIS_USER_TIME_PASSWORD` | `time` | own capability key, `cmd:time`, publish to `telegram:outgoing` |
| `REDIS_USER_HOME_AUTOMATION_PASSWORD` | `home_automation` | own capability key, `cmd:home-automation`, publish to `telegram:outgoing` |

With `REDIS_PASSWORD` set, the `default` user is disabled, each service is
limited to its own keys and channels, and the proxy cannot be used to reach
another service's `cmd:` channel. The proxy embeds `REDIS_PASSWORD` into its
connection automatically; client services use their user in the Redis URL
(e.g. `redis://time:password@redis:6379/0`), which the compose file already
builds from the variables above.

Adding a new client service: declare `REDIS_USER_<NAME>_PASSWORD` and, if the
service id differs from the user name, `REDIS_USER_<NAME>_ID=<service_id>`;
then use `redis://<name>:<password>@redis:6379/0` in its `REDIS_URL`.