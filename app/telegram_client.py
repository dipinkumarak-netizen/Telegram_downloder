from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
import asyncio

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from app.classifier import classify_file, sanitize_filename
from app.config import Settings
from app.database import Database

logger = logging.getLogger(__name__)
QueueCallback = Callable[[int], Awaitable[bool]]
SourceIdsProvider = Callable[[], set[int]]


class TelegramService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        enqueue: QueueCallback,
        source_ids_provider: SourceIdsProvider | None = None,
    ):
        self.settings = settings
        self.database = database
        self.enqueue = enqueue
        self.source_ids_provider = source_ids_provider
        self.client = TelegramClient(
            str(settings.telegram_session_path),
            settings.telegram_api_id,
            settings.telegram_api_hash.get_secret_value(),
            sequential_updates=True,
        )
        self.connected = False
        self.user_id: int | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError(
                "Telegram session is not authorized. Run scripts/telegram_login.py first."
            )
        # Keep one stable handler and apply the current source selection per event so
        # Settings changes take effect without re-registering or restarting Telegram.
        self.client.add_event_handler(self._on_message, events.NewMessage(chats=None))
        self.connected = True
        me = await self.client.get_me()
        self.user_id = me.id
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(
                self._monitor_connection(), name="telegram-connection-monitor"
            )
        logger.info("Telegram connected as user_id=%s", me.id)

    async def stop(self) -> None:
        self._stopping = True
        self.connected = False
        self.user_id: int | None = None
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
            self._monitor_task = None
        await self.client.disconnect()

    async def ensure_connected(self) -> None:
        if self.client.is_connected():
            self.connected = True
            return
        logger.warning("Telegram disconnected; reconnecting client")
        await self.client.connect()
        if not await self.client.is_user_authorized():
            self.connected = False
            raise RuntimeError(
                "Telegram session is not authorized. Run scripts/telegram_login.py first."
            )
        self.connected = True

    async def _monitor_connection(self) -> None:
        while not self._stopping:
            try:
                await self.client.disconnected
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Telegram disconnect monitor error: %s", exc)
            self.connected = False
            if self._stopping:
                break
            logger.warning("Telegram connection lost; waiting for next download attempt to reconnect")

    async def _on_message(self, event: events.NewMessage.Event) -> None:
        message = event.message
        chat_id = event.chat_id
        if chat_id is None or not message.file:
            return
        selected = (
            self.source_ids_provider()
            if self.source_ids_provider is not None
            else set(self.settings.allowed_chat_ids)
        )
        if self.settings.include_saved_messages and self.user_id is not None:
            selected.add(self.user_id)
        if chat_id not in selected:
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
            message_date=message.date.isoformat() if message.date else None,
        )
        if job_id is None:
            await self._reply(message, f"Duplicate skipped: {filename} ({duplicate})")
            return
        await self.database.event(
            "INFO", f"Queued chat_id={chat_id} message_id={message.id} filename={filename}"
        )
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
