# Telegram Command Proxy

One dockerized service that holds the only connection to a Telegram bot. Client
services register their commands with the proxy instead of talking to Telegram
themselves, and a user reaches them through explicit commands or free text.

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

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check src tests examples
.venv/bin/python -m build
```

## Testing

- `docker compose up -d` — proxy + Redis only.
- `docker compose --profile demo up -d --build` — proxy + Redis + demo clients.

## GitHub automation

Recommended automation for this repository is now scaffolded under `.github/workflows/`:

- `ci.yml` runs Ruff, pytest, a package build, and a Docker image build for every pull request plus pushes to `main`.
- `dependency-review.yml` blocks risky dependency changes in pull requests using GitHub's dependency review action.
- `codeql.yml` runs GitHub CodeQL on pull requests, pushes to `main`, and on a weekly schedule.

This set covers the highest-value checks for the current codebase: Python quality, test regressions, packaging drift, container build breakage, and common security issues. Additional automation only becomes worthwhile once releases, deployments, or broader integration tests exist.

## Recommended branch protection and workflow

For a small Python service like this, prefer a trunk-based workflow over GitFlow:

- Create short-lived feature branches from `main`.
- Require pull requests before merging to `main`.
- Require the `CI / Lint, test, and package`, `CI / Build container image`, `Dependency Review`, and `CodeQL` checks to pass.
- Require at least one approving review and enable dismissal of stale approvals after new commits.
- Require branches to be up to date before merge if you want stricter protection against hidden breakage.
- Block force pushes and branch deletion on `main`.

GitFlow is only worth the extra process if you plan to maintain multiple supported release branches at the same time. For the current repository shape, trunk-based development is simpler and gives faster feedback with less branch-management overhead.