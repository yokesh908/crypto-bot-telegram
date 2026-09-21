"""
Fresh Telegram login -> new StringSession written into .env.

Use this when Telegram reports:
    "The authorization key (session file) was used under two different
     IP addresses simultaneously"

That error means the auth key was flagged as duplicated and can never be
reused.  This script performs a brand new login and replaces
TELEGRAM_STRING_SESSION in .env with a clean session.

Run:
    cd /home/yokeshwaran/crypto-bot
    venv/bin/python telegram-bot/fresh_login.py
"""
import os
import re
import sys
import asyncio

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")


def update_env(key, value):
    with open(ENV_PATH) as f:
        text = f.read()

    line = f"{key}={value}"
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)

    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += line + "\n"

    with open(ENV_PATH, "w") as f:
        f.write(text)


async def main():
    load_dotenv(ENV_PATH)

    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")

    if not api_id or not api_hash:
        print("ERROR: TELEGRAM_API_ID / TELEGRAM_API_HASH missing in .env")
        sys.exit(1)

    phone = os.getenv("PHONE_NUMBER", "").strip()
    if not phone:
        phone = input("Enter phone number with country code (e.g. +9198xxxxxxx): ").strip()

    print(f"\nLogging in as {phone} ...")
    client = TelegramClient(
        StringSession(), api_id, api_hash,
        ipv6=False,
        device_model="crypto-bot-telegram",
        system_version="Linux",
        app_version="1.0",
    )

    await client.connect()

    sent = await client.send_code_request(phone)
    code = input("Enter the login code Telegram just sent you: ").strip()

    try:
        await client.sign_in(phone=phone, code=code,
                             phone_code_hash=sent.phone_code_hash)
    except SessionPasswordNeededError:
        password = input("Two-step verification is on. Enter your password: ").strip()
        await client.sign_in(password=password)

    me = await client.get_me()
    session_string = client.session.save()

    update_env("TELEGRAM_STRING_SESSION", session_string)
    update_env("PHONE_NUMBER", phone)

    print("\n" + "=" * 70)
    print(f"SUCCESS - logged in as {me.username or me.first_name} ({me.phone})")
    print("New TELEGRAM_STRING_SESSION saved to .env")
    print("=" * 70)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
