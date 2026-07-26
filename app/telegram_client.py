from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from app.classifier import classify_file, sanitize_filename
from app.config import Settings
from app.database import Database

logger = logging.getLogger(__name__)
QueueCallback = Callable[[int], Awaitable[None]]


class TelegramService:
    def __init__(self, settings: Settings, database: Database, enqueue: QueueCallback):
        self.settings = settings
        self.database = database
        self.enqueue = enqueue
        self.client = TelegramClient(
            str(settings.telegram_session_path),
            settings.telegram_api_id,
            settings.telegram_api_hash.get_secret_value(),
            sequential_updates=False,
        )
        self.connected = False
        self.user_id: int | None = None

    async def start(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError(
                "Telegram session is not authorized. Run scripts/telegram_login.py first."
            )
        allowed = list(self.settings.allowed_chat_ids)
        if self.settings.include_saved_messages:
            allowed.append("me")
        self.client.add_event_handler(self._on_message, events.NewMessage(chats=allowed or None))
        self.connected = True
        me = await self.client.get_me()
        self.user_id = me.id
        logger.info("Telegram connected as user_id=%s", me.id)

    async def stop(self) -> None:
        self.connected = False
        self.user_id: int | None = None
        await self.client.disconnect()

    async def _on_message(self, event: events.NewMessage.Event) -> None:
        message = event.message
        chat_id = event.chat_id
        if chat_id is None or not message.file:
            return
        if chat_id not in self.settings.allowed_chat_ids and not (
            self.settings.include_saved_messages and chat_id == self.user_id
        ):
            logger.warning("Ignoring file from non-allow-listed chat_id=%s", chat_id)
            return
        filename = self.filename_for(message)
        size = int(message.file.size or 0)
        max_bytes = int(self.settings.max_file_size_gb * 1024**3)
        if max_bytes and size > max_bytes:
            await self._reply(message, f"Failed: {filename} exceeds configured maximum size")
            return
        category = classify_file(filename, message.file.mime_type)
        document = message.document
        file_id = str(document.id) if document else None
        unique_id = f"{document.id}:{document.access_hash}:{size}" if document else None
        job_id, duplicate = await self.database.add_download(
            chat_id=chat_id,
            message_id=message.id,
            file_id=file_id,
            file_unique_id=unique_id,
            original_filename=filename,
            file_size=size,
            mime_type=message.file.mime_type,
            category=category,
        )
        if job_id is None:
            await self._reply(message, f"Duplicate skipped: {filename} ({duplicate})")
            return
        await self.database.event("INFO", f"Queued: {filename}")
        await self._reply(message, f"Queued: {filename}")
        await self.enqueue(job_id)

    @staticmethod
    def filename_for(message: Message) -> str:
        if message.file and message.file.name:
            return sanitize_filename(message.file.name)
        ext = message.file.ext if message.file else ""
        return sanitize_filename(
            f"telegram-{message.chat_id}-{message.id}{ext or ''}",
            f"telegram-{message.id}",
        )

    async def _reply(self, message: Message, text: str) -> Message | None:
        if not self.settings.status_replies_enabled:
            return None
        try:
            return await message.reply(text)
        except Exception as exc:
            logger.warning("Could not send Telegram status reply: %s", exc)
            return None
