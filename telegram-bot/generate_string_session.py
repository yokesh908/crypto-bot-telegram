"""
Generate a Telethon StringSession for cloud deployment.

In local mode the bot stores the Telegram session in a .session file on disk.
In the cloud (Render, Fly.io, etc.) the filesystem is ephemeral, so the
session must be stored as an environment variable.  This script converts the
existing session file (if present) — or performs a fresh login — into a
StringSession you can paste into your cloud app's environment variables.

Run:  venv/bin/python telegram-bot/generate_string_session.py
"""
import os
import sys
import asyncio
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")


async def main():
    load_dotenv(ENV_PATH)

    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")

    if not api_id or not api_hash:
        print("ERROR: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")
        print("       Get them from https://my.telegram.org/apps")
        sys.exit(1)

    # Try to use the existing session file first
    session_file = os.path.join(os.path.dirname(__file__), "..", "telegram_session")

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if os.path.exists(session_file):
        print("Found existing session file — converting to StringSession ...")
        client = TelegramClient(session_file, api_id, api_hash)
    else:
        print("No session file found — starting fresh login ...")
        phone = os.getenv("PHONE_NUMBER", "").strip()
        if not phone:
            phone = input(
                "Enter your phone number (with country code, e.g. +9198...): "
            ).strip()

        client = TelegramClient(StringSession(), api_id, api_hash)

    await client.start(phone=phone if not os.path.exists(session_file) else None)

    me = await client.get_me()
    print()
    print(f"Logged in as: {me.username or me.first_name} ({me.phone})")

    string_session = client.session.save()
    print()
    print("=" * 70)
    print("COPY THE STRING SESSION BELOW:")
    print("=" * 70)
    print()
    print(string_session)
    print()
    print("=" * 70)
    print()
    print("Paste this value into your .env file:")
    print()
    print(f"  TELEGRAM_STRING_SESSION={string_session}")
    print()
    print("OR set it as an environment variable in your cloud provider's")
    print("dashboard (e.g. Render, Fly.io).")
    print("=" * 70)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
