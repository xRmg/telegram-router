from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    async def send(self, text: str, level: str = "info") -> int: ...

    async def send_confirmation(self, text: str, confirmation_id: str) -> int: ...

    async def edit(self, message_id: int, text: str) -> None: ...

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None: ...
