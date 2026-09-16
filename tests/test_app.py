from __future__ import annotations

import logging
from types import SimpleNamespace

from telegram_proxy import app
from telegram_proxy.keys import OWNER_CHAT_ID_KEY
from tests.helpers import make_config


class _RedisAdapter:
    def __init__(self, redis) -> None:
        self._redis = redis

    async def get(self, key):
        return await self._redis.get(key)

    async def set(self, key, value):
        return await self._redis.set(key, value)

    async def delete(self, key):
        return await self._redis.delete(key)

    async def aclose(self) -> None:
        pass


class _FakeBot:
    def __init__(self, *_args, **_kwargs) -> None:
        self.session = SimpleNamespace(close=self._close)

    async def _close(self) -> None:
        pass


class _FakeNotifier:
    def __init__(self, _bot, chat_id, logger=None) -> None:
        self.chat_id = chat_id
        self.logger = logger


class _FakeRegistry:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class _FakeToolRouter:
    def __init__(self, *_args, **_kwargs) -> None:
        pass


class _FakeRouter:
    def __init__(self, *_args, **_kwargs) -> None:
        pass


class _FakeOutgoingRelay:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def run(self) -> None:
        pass


class _FakeProxyBot:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def run(self) -> None:
        pass


def _patch_run_dependencies(monkeypatch, redis) -> None:
    monkeypatch.setattr(app.Redis, "from_url", lambda *_args, **_kwargs: _RedisAdapter(redis))
    monkeypatch.setattr(app, "Bot", _FakeBot)
    monkeypatch.setattr(app, "TelegramNotifier", _FakeNotifier)
    monkeypatch.setattr(app, "CapabilityRegistry", _FakeRegistry)
    monkeypatch.setattr(app, "ToolRouter", _FakeToolRouter)
    monkeypatch.setattr(app, "Router", _FakeRouter)
    monkeypatch.setattr(app, "OutgoingRelay", _FakeOutgoingRelay)
    monkeypatch.setattr(app, "ProxyBot", _FakeProxyBot)


async def test_run_clears_invalid_owner_chat_id_from_redis(redis, monkeypatch, caplog):
    await redis.set(OWNER_CHAT_ID_KEY, "not-an-int")
    _patch_run_dependencies(monkeypatch, redis)
    caplog.set_level(logging.WARNING, logger="telegram_proxy")

    await app.run(make_config(telegram_owner_chat_id=None))

    assert await redis.get(OWNER_CHAT_ID_KEY) is None
    assert "owner_chat_id_invalid_in_redis" in caplog.text


async def test_run_does_not_log_learn_token(redis, monkeypatch, caplog, capsys):
    _patch_run_dependencies(monkeypatch, redis)
    monkeypatch.setattr(app.secrets, "token_hex", lambda _n: "deadbeef")
    caplog.set_level(logging.INFO, logger="telegram_proxy")

    await app.run(make_config(telegram_owner_chat_id=None))

    assert "learn_owner_mode_active" in caplog.text
    assert "deadbeef" not in caplog.text
    assert "deadbeef" in capsys.readouterr().out
