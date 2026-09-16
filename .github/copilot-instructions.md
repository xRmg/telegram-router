# Repository instructions for Copilot

## Repository overview

- This repository is a Python 3.12 Telegram router/proxy with two main packages:
  - `src/telegram_proxy`: the bot, routing, registry, notifier, and Redis-backed runtime.
  - `src/telegram_proxy_client`: the client SDK that registers capabilities and handles commands.
- Redis is part of the core contract. Command dispatch, capability registration, heartbeats, and outgoing replies all depend on stable channel/key behavior.
- `examples/` contains runnable reference clients and should remain consistent with the public SDK and README examples.

## Default development commands

- Create an environment and install dev dependencies with `python -m pip install -e ".[dev]"`.
- Run lint checks with `ruff check src tests examples`.
- Run tests with `pytest`.
- Validate packaging with `python -m build`.

## Change expectations

- Prefer small, typed, async-safe changes. Do not introduce blocking I/O in request, routing, bot polling, heartbeat, or Redis listener paths.
- Preserve backward-compatible Redis message shapes, capability payload fields, and command semantics unless the task explicitly requires a protocol change.
- Keep configuration environment-driven through `Config` and `.env.example`; document any new settings there and in `README.md`.
- Add or update tests near the affected behavior whenever router, config, client SDK, command parsing, or outgoing relay logic changes.
- When changing user-facing commands, help output, examples, or configuration steps, update both tests and `README.md`.

## Review focus

- Check routing changes for timeout handling, confirmation flow, and service availability edge cases.
- Check client SDK changes for registration TTL/heartbeat correctness and reply/error behavior.
- Check logging and notifier changes for accidental leakage of sensitive user or LLM content.
- Keep Docker and local development commands working without requiring non-documented setup beyond Redis and environment variables.
