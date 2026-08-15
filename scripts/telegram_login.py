from __future__ import annotations

import asyncio
import getpass

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from app.config import get_settings


async def login() -> None:
    settings = get_settings()
    session = settings.telegram_session_path
    session.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(session), settings.telegram_api_id, settings.telegram_api_hash.get_secret_value()
    )
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"Session is already authorized for Telegram user ID {me.id}.")
            return
        phone = settings.telegram_phone or input(
            "Phone number (international format, e.g. +919...): "
        ).strip()
        sent = await client.send_code_request(phone)
        code = getpass.getpass("Telegram OTP (input hidden): ").strip()
        try:
            await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            password = getpass.getpass("Telegram 2FA password (input hidden): ")
            await client.sign_in(password=password)
        me = await client.get_me()
        print(f"Login successful. Session saved for Telegram user ID {me.id}.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(login())
