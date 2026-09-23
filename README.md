# Telegram Command Proxy

One dockerized service that holds the only connection to a Telegram bot. Client
services register their commands with the proxy instead of talking to Telegram
themselves, and a user reaches them through explicit commands or free text.

## How it works

```mermaid
graph TD
    U["👤 User"] -->|message| B["🤖 Telegram Bot"]
    B <-->|long polling| P["📡 Proxy & Router"]

    subgraph PATHS["Message Routing"]
        direction TD
        
        subgraph EXPL["Explicit Command: /prefix command"]
            E1["Parse /prefix"] --> E2{"Service<br/>live?"}
            E2 -->|no| E_ERR["⚠️ Unavailable"]
            E2 -->|yes| E3{"Confirm<br/>needed?"}
            E3 -->|yes| E_PEND["📋 Pending"]
            E3 -->|no| E_DISP["Dispatch"]
        end

        subgraph FREE["Free Text: Intelligent Routing"]
            F0{"LLM<br/>enabled?"}
            F0 -->|no| F_OFF["ℹ️ Disabled"]
            F0 -->|yes| F1{"Decision model<br/>configured?"}
            
            F1 -->|no| F_CHAT["🧠 LLM:<br/>select &amp; fill"]
            
            F1 -->|yes| F_STRAT{"ROUTING_<br/>STRATEGY?"}
            F_STRAT -->|model| F_CHAT
            F_STRAT -->|decision-<br/>select| F_JEV_SEL["⚡ Jev selects<br/>🧠 LLM fills params"]
            F_STRAT -->|decision-<br/>extract| F_JEV_EXT["⚡ Jev selects &amp;<br/>fills extractable<br/>🧠 LLM fills rest"]
            F_STRAT -->|decision-<br/>only| F_JEV_ONLY["⚡ Jev selects &amp;<br/>fills all<br/>no LLM fallback"]
            
            F_CHAT --> F_CONF["Confidence<br/>score"]
            F_JEV_SEL --> F_CONF
            F_JEV_EXT --> F_CONF
            F_JEV_ONLY --> F_CONF
            
            F_CONF -->|≥ threshold| F4{"Confirm<br/>needed?"}
            F_CONF -->|&lt; threshold| F_UNSURE["🤷 Not sure"]
            F4 -->|yes| F_PEND["📋 Pending"]
            F4 -->|no| F_DISP["Dispatch"]
        end

        E_ERR --> B
        E_PEND --> B
        E_DISP --> REDIS
        F_OFF --> B
        F_UNSURE --> B
        F_PEND --> B
        F_DISP --> REDIS
    end

    subgraph REDIS["Redis"]
        REG["📦 capabilities:id<br/>TTL 90s · heartbeat 30s"]
        CH["🔌 channels &amp; keys"]
    end

    REDIS --> CH
    CH -->|cmd:id| SVC["🔧 Services"]
    REG <-->|register &amp; refresh| SVC
    SVC -->|reply/push| CH
    CH -->|telegram:outgoing| RELAY["📤 Relay<br/>prefix · rate limit"]
    RELAY --> B
```

### Topology

```mermaid
graph TB
    subgraph CLOUD["☁️ Telegram Cloud"]
        API["Telegram Bot API"]
    end

    API <-->|HTTPS<br/>long polling| PROXY["🔀 Proxy &amp; Router"]

    subgraph STACK["🐳 Docker Compose Stack"]
        direction TB
        
        PROXY
        
        subgraph STORAGE["Data Layer"]
            REDIS["🗄️ Redis<br/>(capabilities,<br/>channels, replies)"]
        end
        
        subgraph MODELS["🧠 AI Layer"]
            LLM["LLM Provider<br/>(OpenAI-compatible)<br/>optional"]
            DECISIONS["⚡ Decisions API<br/>(Jev structured model)<br/>optional<br/>OpenRouter"]
        end
        
        PROXY <-->|Redis protocol| REDIS
        PROXY -->|tool calls| LLM
        PROXY -->|choice/score<br/>questions| DECISIONS
        
        subgraph SERVICES["🔧 Client Services"]
            direction LR
            TIME["⏰ time-service<br/>cmd:time"]
            HA["🏠 home-automation<br/>cmd:ha"]
            MORE["📦 more services<br/>cmd:*"]
        end
        
        REDIS <-->|capabilities<br/>commands<br/>replies| SERVICES
    end
    
    style CLOUD fill:#e1f5ff
    style STACK fill:#f1f8e9
    style MODELS fill:#fff3e0
    style STORAGE fill:#fce4ec
```

The proxy is the only service that talks to Telegram; every client service
reaches the user exclusively through Redis and the proxy.

### Message flow

```mermaid
sequenceDiagram
    participant U as User
    participant T as Telegram
    participant P as Proxy
    participant R as Redis
    participant D as Jev<br/>Decisions API
    participant L as LLM
    participant S as Service

    Note over S: 📦 Startup & heartbeat
    S->>R: SET capabilities:id (TTL 90s)
    loop every 30s
        S->>R: refresh capability key
    end

    rect rgb(220, 240, 255)
    Note over U,S: 🎯 Explicit command path: /time now
    end
    U->>T: /time now
    T->>P: message
    P->>R: capabilities:time live?
    R-->>P: ✓ yes
    P->>R: PUBLISH cmd:time {request_id, params}
    R-->>S: deliver
    S->>R: PUBLISH telegram:outgoing {reply}
    R-->>P: reply received
    P->>T: send reply (prefixed)
    T-->>U: ✓ Time: 2026-09-21 10:00:00

    rect rgb(240, 220, 255)
    Note over U,S: 🧠 Free text routing (no Jev)
    end
    U->>T: "turn on lights"
    T->>P: message
    P->>L: select capability + params
    L-->>P: home-automation.lights_on {room=kitchen}
    P->>R: check live + PUBLISH
    R-->>S: deliver
    S->>R: reply
    R-->>P: received
    P->>T: reply
    T-->>U: ✓ Lights on

    rect rgb(255, 220, 220)
    Note over U,S: ⚡ Free text routing (with Jev)
    end
    U->>T: "turn on the kitchen lights"
    T->>P: message
    par Jev Stage
        P->>D: which capability?
        D-->>P: home-automation (conf: 0.95)
    end
    alt decision-select
        par Parameter filling
            P->>L: extract room parameter
            L-->>P: room=kitchen
        end
    else decision-extract or decision-only
        par Parameter extraction
            P->>D: extract room parameter
            D-->>P: room=kitchen (conf: 0.89)
        end
    end
    P->>R: check live + PUBLISH
    R-->>S: deliver
    S->>R: reply
    R-->>P: received
    P->>T: reply (models + cost logged)
    T-->>U: ✓ Lights on in kitchen

    rect rgb(255, 240, 220)
    Note over S: 📬 Unsolicited push
    end
    S->>R: PUBLISH telegram:outgoing {text, level}
    R-->>P: received
    P->>T: send push (rate-limited)
    T-->>U: 📬 Sniper: order filled
```

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

Optionally set `STRUCTURED_DECISION_MODEL` to a structured decision model on
OpenRouter's Decisions API (e.g. `typesafe/jev-1.13`) to speed up capability
selection: the router asks that model which capability fits first, and only
calls `LLM_MODEL` afterwards to extract parameters when one is picked. A
clear non-match skips the `LLM_MODEL` call entirely. Leave it unset to route
with `LLM_MODEL` alone.

`ROUTING_STRATEGY` picks how much of the work the decision model does:

| Strategy | Selects capability | Fills parameters | Calls `LLM_MODEL` |
|---|---|---|---|
| `model` (default) | `LLM_MODEL` | `LLM_MODEL` | always |
| `decision-select` | decision model | `LLM_MODEL` | on a match |
| `decision-extract` | decision model | decision model where it can | for the rest |
| `decision-only` | decision model | decision model | never |

The three `decision-*` values require `STRUCTURED_DECISION_MODEL` and are
rejected at startup without it.

A command's parameters can be filled by the decision model only when *every*
one is declared with `enum=[...]` (it picks a canonical value) or
`extract="span"` (it picks a literal span from the message). Any command with
a parameter that needs transforming — a date to normalise, a number to parse —
falls to `LLM_MODEL`, as does any extraction reported below
`STRUCTURED_DECISION_MIN_CONFIDENCE`. Under `decision-only` there is no
fallback, so those commands reply asking for the explicit `/<prefix> <command>`
form instead.

Prefer `enum` where the value set is known: the returned value is canonical,
so a service never receives `"the Big Apple"` when it expects `"New York"`.

Set `ROUTING_VERBOSE=true` to have the bot reply to every free-text message
with the models, timings and cost it used. The same data is written to the
structured log on every routed message regardless (`models`, `cost_usd`,
`routing_seconds`), so `docker compose logs proxy` answers it without the
chat noise.

## Usage

| Message | Effect |
|---|---|
| `/help` | Router usage and list of services |
| `/help <service>` | One service's help text |
| `/capabilities` | Every registered capability in detail |
| `/configured` | Which models are configured, and which model routes each command |
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

Redis is protected by a single shared password. By default nothing is
enforced (dev mode); to enable authentication, set `REDIS_PASSWORD` in `.env`
and restart:

```bash
docker compose --profile demo up -d
```

With `REDIS_PASSWORD` set, every connection — proxy and client services —
must authenticate with it, and unauthenticated access (e.g. `redis-cli ping`
without a password) is rejected. The proxy embeds the password into its
connection automatically; client services pick it up from the
`REDIS_PASSWORD` environment variable passed through by the compose file.

Per-service isolation (each service limited to its own keys and channels) is
planned; for now all services share one credential.