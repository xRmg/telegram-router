from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .commands import parse_explicit
from .config import RESERVED_PREFIXES, Config
from .help_text import build_general_help, build_service_help
from .keys import LAST_UPDATE_ID_KEY, OWNER_CHAT_ID_KEY
from .registry import CapabilityRegistry, resolve_display_name
from .router import Router

_CONFIRMATION_OUTCOMES = {
    "dispatched": "Confirmed.",
    "cancelled": "Cancelled.",
    "expired": "This confirmation expired.",
    "service_unavailable": "Service unavailable.",
    "unknown_command": "Command unavailable.",
    "confirmation_requested": "A confirmation is already pending.",
}


class TelegramNotifier:
    def __init__(
        self,
        bot: Bot,
        chat_id: int | None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._bot = bot
        self.chat_id = chat_id
        self._logger = logger or logging.getLogger(__name__)

    async def send(self, text: str, level: str = "info") -> int:
        message = await self._bot.send_message(
            chat_id=self.chat_id,
            text=text,
            disable_notification=level == "info",
        )
        return message.message_id

    async def send_confirmation(self, text: str, confirmation_id: str) -> int:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Yes", callback_data=f"confirm:{confirmation_id}:yes"
                    ),
                    InlineKeyboardButton(text="No", callback_data=f"confirm:{confirmation_id}:no"),
                ]
            ]
        )
        message = await self._bot.send_message(chat_id=self.chat_id, text=text, reply_markup=markup)
        return message.message_id

    async def edit(self, message_id: int, text: str) -> None:
        try:
            await self._bot.edit_message_text(
                chat_id=self.chat_id, message_id=message_id, text=text
            )
        except TelegramBadRequest as exc:
            self._logger.warning("edit_message_failed", extra={"error": str(exc)})
        try:
            await self._bot.edit_message_reply_markup(chat_id=self.chat_id, message_id=message_id)
        except TelegramBadRequest as exc:
            self._logger.warning("edit_markup_failed", extra={"error": str(exc)})

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        try:
            await self._bot.answer_callback_query(callback_query_id=callback_id, text=text)
        except TelegramBadRequest as exc:
            self._logger.warning("answer_callback_failed", extra={"error": str(exc)})


class ProxyBot:
    def __init__(
        self,
        bot: Bot,
        config: Config,
        router: Router,
        registry: CapabilityRegistry,
        notifier: TelegramNotifier,
        redis: Any,
        logger: logging.Logger | None = None,
    ) -> None:
        self._bot = bot
        self._config = config
        self._router = router
        self._registry = registry
        self._notifier = notifier
        self._redis = redis
        self._logger = logger or logging.getLogger(__name__)
        self._dispatcher = Dispatcher()
        self._dispatcher.message.register(self._handle_message)
        self._dispatcher.callback_query.register(self._handle_callback)

    async def run(self) -> None:
        await self._bot.delete_webhook()
        offset = await self._load_offset()
        while True:
            try:
                updates = await self._bot.get_updates(
                    offset=offset, timeout=30, allowed_updates=["message", "callback_query"]
                )
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
                continue
            except TelegramUnauthorizedError:
                self._logger.error("telegram_unauthorized")
                return
            except TelegramNetworkError as exc:
                self._logger.warning("telegram_network_error", extra={"error": str(exc)})
                await asyncio.sleep(2)
                continue
            for update in updates:
                try:
                    await self._dispatcher.feed_update(self._bot, update)
                except Exception:
                    self._logger.exception(
                        "update_processing_failed", extra={"update_id": update.update_id}
                    )
                offset = update.update_id + 1
                await self._redis.set(LAST_UPDATE_ID_KEY, update.update_id)

    async def _load_offset(self) -> int | None:
        raw = await self._redis.get(LAST_UPDATE_ID_KEY)
        if raw is None:
            return None
        try:
            return int(raw) + 1
        except (TypeError, ValueError):
            return None

    async def _handle_message(self, message: Message) -> None:
        if self._config.learn_owner_mode:
            await self._handle_learn_mode(message)
            return
        if message.chat.id != self._config.telegram_owner_chat_id:
            self._logger.warning("message_ignored", extra={"chat_id": message.chat.id})
            return
        text = (message.text or "").strip()
        if not text:
            return
        explicit = parse_explicit(text)
        if explicit is not None and explicit.prefix in RESERVED_PREFIXES:
            if explicit.prefix == "help":
                await self._notifier.send(self._help_text(explicit.rest))
            else:
                await self._notifier.send(self._capabilities_text())
            return
        await self._router.handle_text(text)

    async def _handle_learn_mode(self, message: Message) -> None:
        text = (message.text or "").strip()
        token = self._config.learn_token
        if token and text != token:
            self._logger.warning(
                "learn_mode_wrong_token", extra={"chat_id": message.chat.id}
            )
            return
        chat_id = message.chat.id
        await self._redis.set(OWNER_CHAT_ID_KEY, chat_id)
        self._notifier.chat_id = chat_id
        self._logger.info("owner_chat_learned", extra={"chat_id": chat_id})
        await self._notifier.send(
            f"Owner chat learned (id={chat_id}). "
            f"Set TELEGRAM_OWNER_CHAT_ID={chat_id} and restart."
        )

    async def _handle_callback(self, callback: CallbackQuery) -> None:
        message = callback.message
        if message is None:
            await self._notifier.answer_callback(callback.id)
            return
        if self._config.learn_owner_mode:
            await self._notifier.answer_callback(callback.id)
            return
        if message.chat.id != self._config.telegram_owner_chat_id:
            await self._notifier.answer_callback(callback.id)
            return
        parts = (callback.data or "").split(":", 2)
        if len(parts) != 3 or parts[0] != "confirm":
            await self._notifier.answer_callback(callback.id)
            return
        confirmation_id, action = parts[1], parts[2]
        outcome = await self._router.handle_confirmation(confirmation_id, action == "yes")
        text = _CONFIRMATION_OUTCOMES.get(outcome, "Unavailable.")
        await self._notifier.answer_callback(callback.id, text)
        await self._notifier.edit(message.message_id, text)

    def _help_text(self, service_ref: str) -> str:
        if service_ref:
            return build_service_help(self._config, self._registry, service_ref)
        return build_general_help(self._config, self._registry)

    def _capabilities_text(self) -> str:
        lines = ["Registered services:"]
        for capability in self._registry.all():
            name = resolve_display_name(capability.service_id, self._config, self._registry)
            lines.append(
                f"- {name} (service_id={capability.service_id}, prefix=/{capability.prefix})"
            )
            if not capability.commands:
                lines.append("  output only, no commands")
                continue
            for command in capability.commands:
                suffix = " [confirm]" if command.confirm else ""
                lines.append(
                    f"  /{capability.prefix} {command.name}{suffix}: {command.description}"
                )
                for param_name, spec in command.parameters.items():
                    required = "required" if spec.required else "optional"
                    lines.append(f"    {param_name} ({spec.type}, {required}): {spec.description}")
        if len(lines) == 1:
            lines.append("- none")
        return "\n".join(lines)
