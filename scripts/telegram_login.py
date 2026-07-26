from __future__ import annotations

import asyncio
import getpass
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


async def login() -> None:
    api_id_raw = os.environ.get("TELEGRAM_API_ID") or input("Telegram API ID: ").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH") or getpass.getpass("Telegram API Hash: ")
    session = Path(os.environ.get("TELEGRAM_SESSION_PATH", "/app/session/downloader"))
    session.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session), int(api_id_raw), api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"Session is already authorized for Telegram user ID {me.id}.")
            return
        phone = input("Phone number (international format, e.g. +919...): ").strip()
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
