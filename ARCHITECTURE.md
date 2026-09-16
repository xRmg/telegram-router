# Telegram Command Proxy

## Project

This project adds one dockerized service to the home stack: a Telegram proxy. The proxy holds the only connection to a Telegram bot. Other services on the stack do not talk to Telegram directly. They register their commands and capabilities with the proxy instead.

A user can reach any registered service in two ways:

- An explicit command, such as `/ha` or `@home`.
- A free-text message, such as "turn on the lights," routed to the correct service by an LLM.

**Why this exists:** Telegram allows only one active connection per bot. Centralizing that connection avoids duplicating connection handling and rate-limit logic across every service. It also lets a user issue a command in plain language, instead of learning an exact syntax per service.

## Architecture

### Components

- **Telegram proxy / router** — one dockerized service. Holds the bot connection. Sends every reply to one fixed owner chat. Runs both routing paths described below.
- **Client services** — for example, a home-automation service. Each one publishes its own capabilities to Redis, listens for commands on its own channel, and publishes replies back.
- **Redis** — carries all messages between the proxy and the client services. Also stores the live capability registry.

### Transport: long polling

The proxy connects to Telegram with long polling, not a webhook.

- A webhook needs a public HTTPS endpoint. That means a reverse proxy and a TLS certificate — infrastructure this stack does not otherwise need.
- A webhook also creates a failure mode: if routing takes too long, Telegram times out and resends the same update. Redis pub/sub has no deduplication, so a resend can trigger the same command twice.
- Long polling needs only outbound internet access, and has no such timeout constraint.

The proxy tracks the last processed Telegram update ID in the Redis key `telegram:last_update_id`, overwritten after every successful poll, so a restart never reprocesses old messages.

### Two routing paths

**1. Explicit command.** The proxy matches a prefix, such as `/ha`, against a service's registered prefix. It dispatches the command directly. No LLM call happens.

**2. Free text.** The router sends the message, together with every live registered capability, to an LLM through an OpenAI-compatible tool-calling API. Tool-calling has no native confidence score, so the router adds one itself: a required numeric `confidence` argument on every tool's parameter schema, alongside whatever arguments the command itself defines. The LLM cannot call a tool without also supplying that value — the router never has to parse it out of free-form text. The name `confidence` is reserved on every tool; a command cannot define its own parameter with that name.

- If the score meets a configurable threshold, the command proceeds.
- If the score is too low, the router does nothing except tell the user it is not sure what they mean.
- If the per-minute LLM call limit (`LLM_RATE_LIMIT_PER_MINUTE`) is already used up, the router skips the LLM call entirely and tells the user to try again shortly, rather than queuing the message or failing silently.

Each capability is sent to the LLM under a namespaced tool name, `<service_id>.<command>`. This stops two services from ever presenting an identical tool name to the LLM.

### Confirmation for risky actions

A client service can mark any one of its commands as `confirm: true`. When such a command matches, the router does not dispatch it right away.

1. The router stores the pending action under `pending:<confirmation_id>`, holding `service_id`, `command`, and `parameters`, with a 60-second expiry.
2. The proxy sends the user a Telegram message with a Yes/No inline button.
3. "Yes" dispatches the command. "No," or a timeout, discards it. A button press that arrives after the 60-second expiry is answered as expired, never dispatched.

A self-reported confidence score from an LLM is not a calibrated safety check. This confirmation step is the real safety control for any command with a real-world consequence.

### Capability registration

Each client service writes its capabilities as JSON into `capabilities:<service_id>`, and refreshes that key every 30 seconds. The key carries a 90-second expiry — three missed heartbeats — so a crashed service drops out of the registry on its own, with no manual cleanup.

The router observes the registry through three mechanisms, not one:

1. A full `SCAN capabilities:*` on the router's own startup.
2. A `capabilities:changed` pub/sub message, published by a client whenever it (re)registers and carrying its `service_id`. The router re-reads `capabilities:<service_id>` on receipt, so its cache updates immediately instead of on the next poll.
3. A Redis keyspace notification on key expiry, so a service that goes silent without deregistering is noticed the moment its TTL lapses, not on the next scan. This requires Redis to be started with `notify-keyspace-events Ex` (set in `redis.conf` or the Compose command); without it, expiry events are never delivered and a crashed service would linger in the router's cache.

Capability JSON fields:

| Field | Purpose |
|---|---|
| `schema_version` | Lets the router reject a capability payload it does not recognize. The current accepted value is `1`. |
| `service_id` | Identifies the owning service. |
| `prefix` | Explicit-command prefix, e.g. `ha`. |
| `commands[].name` | Command identifier. |
| `commands[].description` | Plain-language description, read by the LLM. |
| `commands[].examples` | Sample phrases, read by the LLM. |
| `commands[].usage` | Complete invocation shown in help as a clickable example, e.g. `lights_on room=living room`. Optional — falls back to the bare command name. |
| `commands[].confirm` | Whether this command needs Yes/No confirmation before it runs. |
| `commands[].parameters` | Named parameters, each with a `type` of `string`, `number`, or `boolean`, a description, and a required flag. |
| `display_name` | Short label used to prefix this service's messages. Optional — falls back to `service_id` if omitted. |
| `help` | Free-form help text shown by `/help <prefix>`. Optional — falls back to a generated command listing. |

The prefixes `help` and `capabilities` are reserved for the router itself and cannot be claimed by a client service.

A service with no user-invokable commands — for example, one that only pushes alerts — registers with an empty `commands` list. It still appears in `/capabilities` and still carries a heartbeat, so an operator can tell if it has silently died, but it is never a target for either routing path.

### Message contracts

Command, published by the router to `cmd:<service_id>`:

```json
{ "request_id": "a1b2c3", "command": "lights_on", "parameters": { "room": "living room" } }
```

Reply, published by a client service to `telegram:outgoing`:

```json
{ "service_id": "home-automation", "request_id": "a1b2c3", "status": "ok", "text": "Lights turned on in the living room" }
```

`service_id` is required on every outgoing message. `request_id` and `status` are required for a reply to a command, and both are omitted for an unsolicited push (see Output-only services below). `status` is `"ok"` or `"error"` — the field a service uses to signal that a command failed, as distinct from a normal reply. The router logs the outcome either way; for v1, an error reply is relayed to the user as-is, with no automatic retry.

The chat destination is not part of this contract. It is fixed by one configuration value on the proxy, since the bot only ever runs in one chat. This removes an entire class of routing bugs, and it stops a client service from ever misdirecting a reply to the wrong chat.

Before it dispatches a command, the router checks that the target service is still live in the capability registry. If not, it tells the user immediately, instead of waiting out a timeout. If a live service still does not reply within a global, configurable timeout, the router sends its own fallback message.

### Output-only services

A service with nothing to reply to — for example, one that only reports status or alerts — publishes to the same `telegram:outgoing` channel, with no `request_id`:

```json
{ "service_id": "sniper-bot", "text": "Order filled: bought 50 XYZ", "level": "info" }
```

- The proxy relays it straight to the owner chat, since there is no pending command to match it against.
- `level` (`info`, `warning`, or `critical`) is optional and defaults to `info`. The proxy sends `info` silently (no notification sound) and `warning`/`critical` as a normal alert, so routine status pings do not compete with a real problem for your attention.
- Each `service_id` is rate-limited per minute. A service that exceeds its limit has its remaining messages for that window collapsed into one summary line, instead of flooding the chat or being dropped without a trace.

### Display names

Every outgoing message is prefixed with the sending service's short name before it reaches Telegram, e.g. `HA: Lights turned on in the living room`. This applies to replies and to unsolicited pushes alike, since a user reading a free-text-routed reply often cannot otherwise tell which service answered.

The name is resolved in this order:

1. An operator override in the proxy's `SERVICE_DISPLAY_NAMES` configuration, keyed by `service_id`.
2. The `display_name` the client suggested at registration.
3. The raw `service_id`, as a last resort.

The prefix is added once, centrally, by the proxy — client services send only the raw `text` and never format their own prefix. This keeps formatting consistent and lets an operator rename a service's label without redeploying it. The prefix is plain text for v1; Telegram's MarkdownV2 bold formatting is a possible later addition, deferred because it requires careful escaping of the message body.

### Redis keys and channels

One reference for every key and channel used above:

| Key / channel | Kind | Purpose | Expiry |
|---|---|---|---|
| `capabilities:<service_id>` | key | One service's capability JSON | 90s, refreshed every 30s |
| `capabilities:changed` | pub/sub | Notifies the router of an immediate registry change | — |
| `pending:<confirmation_id>` | key | Command awaiting Yes/No confirmation | 60s |
| `telegram:last_update_id` | key | Last processed Telegram update ID | none |
| `cmd:<service_id>` | pub/sub | Commands dispatched to one service | — |
| `telegram:outgoing` | pub/sub | Replies and unsolicited pushes back to the proxy | — |
| `ratelimit:llm` | key | Fixed-window counter for LLM calls | 60s |
| `ratelimit:notify:<service_id>` | key | Fixed-window counter for one service's unsolicited pushes | 60s |

### Logging

Every routing decision is written as one structured log entry: request ID, matched service and command, confidence score, and outcome. The raw user message and the raw LLM response are tagged separately as sensitive content, so they can carry stricter retention or access rules without affecting the rest of the log.

## Tech stack

**Settled:**

- **Docker / Docker Compose** — runs every service.
- **Redis** — message transport and capability registry.
- **Telegram Bot API**, long polling — the only channel between the proxy and the user.
- **An OpenAI-compatible tool-calling API** — reached through a configurable base URL, so the LLM provider is a configuration change, not a code change.
  - **OpenRouter** — the LLM provider for v1, model set to `openrouter/auto`.
  - A local model server (Ollama, vLLM) is a drop-in alternative later, through the same base URL.
- **Python 3.12+** — the implementation language, given its strong support for the pieces above (an async Telegram client, `redis.asyncio`, and the OpenAI Python SDK all work directly against an OpenAI-compatible endpoint).

## Configuration

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_OWNER_CHAT_ID=...
REDIS_URL=redis://redis:6379/0
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=...
LLM_MODEL=openrouter/auto
ROUTING_CONFIDENCE_THRESHOLD=0.6
REPLY_TIMEOUT_SECONDS=10
LLM_RATE_LIMIT_PER_MINUTE=...
NOTIFIER_RATE_LIMIT_PER_MINUTE=...
SERVICE_DISPLAY_NAMES={"sniper-bot": "Sniper", "home-automation": "HA"}
```

## Status

Design phase, including a full adversarial review pass, as of 2026-09-16. Stack confirmed. No code written yet.
