"""Tests for the learn-owner-mode token + Redis-persistence feature."""
from __future__ import annotations

import fakeredis.aioredis
import pytest

from telegram_proxy.bot import ProxyBot
from telegram_proxy.keys import OWNER_CHAT_ID_KEY
from telegram_proxy.ratelimit import FixedWindowLimiter
from telegram_proxy.registry import CapabilityRegistry
from telegram_proxy.router import Router
from tests.helpers import FakeLLM, FakeNotifier, make_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot(config, redis, notifier):
    registry = CapabilityRegistry(redis)
    router = Router(redis, registry, config, notifier, FakeLLM(), FixedWindowLimiter(redis))
    return ProxyBot(bot=None, config=config, router=router, registry=registry,
                    notifier=notifier, redis=redis)


class _FakeMessage:
    """Minimal stand-in for aiogram Message."""

    def __init__(self, chat_id: int, text: str = "") -> None:
        self.chat = _FakeChat(chat_id)
        self.text = text
        self.message_id = 1


class _FakeChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class _FakeCallback:
    """Minimal stand-in for aiogram CallbackQuery."""

    def __init__(self, chat_id: int, data: str = "") -> None:
        self.id = "cb1"
        self.message = _FakeMessage(chat_id)
        self.data = data


# ---------------------------------------------------------------------------
# Token-based learn-mode (Option A)
# ---------------------------------------------------------------------------


async def test_correct_token_persists_chat_id_and_replies(redis):
    notifier = FakeNotifier()
    config = make_config(telegram_owner_chat_id=None, learn_token="abc123")
    bot = _make_bot(config, redis, notifier)

    await bot._handle_message(_FakeMessage(chat_id=777, text="abc123"))

    # Chat id written to Redis
    assert await redis.get(OWNER_CHAT_ID_KEY) == "777"
    # Notifier received the confirmation message
    assert len(notifier.sent) == 1
    assert "777" in notifier.sent[0][0]
    assert "TELEGRAM_OWNER_CHAT_ID" in notifier.sent[0][0]


async def test_wrong_token_is_silently_rejected(redis):
    notifier = FakeNotifier()
    config = make_config(telegram_owner_chat_id=None, learn_token="abc123")
    bot = _make_bot(config, redis, notifier)

    await bot._handle_message(_FakeMessage(chat_id=777, text="wrongtoken"))

    assert await redis.get(OWNER_CHAT_ID_KEY) is None
    assert notifier.sent == []


async def test_no_token_required_when_learn_token_empty(redis):
    """If learn_token is '' any message activates learn mode (backward-compat fallback)."""
    notifier = FakeNotifier()
    config = make_config(telegram_owner_chat_id=None, learn_token="")
    bot = _make_bot(config, redis, notifier)

    await bot._handle_message(_FakeMessage(chat_id=42, text="anything"))

    assert await redis.get(OWNER_CHAT_ID_KEY) == "42"
    assert len(notifier.sent) == 1


async def test_only_the_exact_token_matches(redis):
    notifier = FakeNotifier()
    config = make_config(telegram_owner_chat_id=None, learn_token="abc123")
    bot = _make_bot(config, redis, notifier)

    # Token with trailing content is rejected
    await bot._handle_message(_FakeMessage(chat_id=1, text="abc123 extra"))
    assert notifier.sent == []

    # Leading/trailing whitespace is normalised, so " abc123 " IS accepted
    await bot._handle_message(_FakeMessage(chat_id=1, text=" abc123 "))
    assert len(notifier.sent) == 1


# ---------------------------------------------------------------------------
# Callbacks during learn mode are silently dropped (no chat_id set yet)
# ---------------------------------------------------------------------------


async def test_callback_during_learn_mode_is_ignored(redis):
    notifier = FakeNotifier()
    config = make_config(telegram_owner_chat_id=None, learn_token="tok")
    bot = _make_bot(config, redis, notifier)

    await bot._handle_callback(_FakeCallback(chat_id=99, data="confirm:abc:yes"))

    assert notifier.answers == [("cb1", None)]
    assert notifier.edits == []


# ---------------------------------------------------------------------------
# Normal mode: messages from non-owner are rejected
# ---------------------------------------------------------------------------


async def test_normal_mode_non_owner_message_ignored(redis):
    notifier = FakeNotifier()
    config = make_config(telegram_owner_chat_id=42)
    bot = _make_bot(config, redis, notifier)

    await bot._handle_message(_FakeMessage(chat_id=999, text="hi"))

    assert notifier.sent == []


# ---------------------------------------------------------------------------
# Persistence: Redis-stored chat id survives restart (tested at the bot layer)
# ---------------------------------------------------------------------------


async def test_notifier_chat_id_set_after_learning(redis):
    notifier = FakeNotifier()
    config = make_config(telegram_owner_chat_id=None, learn_token="tok")
    bot = _make_bot(config, redis, notifier)

    await bot._handle_message(_FakeMessage(chat_id=321, text="tok"))

    # Notifier should now address the new owner
    assert notifier.chat_id == 321
