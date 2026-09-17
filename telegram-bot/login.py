"""
One-time Telegram login helper.

Makes first login easier: reads PHONE_NUMBER from .env,
so you only need to type the 6-digit verification code.

Run:  venv/bin/python login.py
"""
import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient


async def main():
    root = os.path.dirname(os.path.dirname(__file__))
    load_dotenv(os.path.join(root, ".env"))

    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    phone = os.getenv("PHONE_NUMBER", "").strip()

    session_file = os.path.join(root, "telegram_session")

    client = TelegramClient(session_file, api_id, api_hash)

    print("=" * 70)
    print("TELEGRAM LOGIN SETUP (one time)")
    print("=" * 70)

    await client.connect()

    if not await client.is_user_authorized():
        if not phone:
            phone = input("Enter your phone number (with country code, "
                          "e.g. +9198...): ").strip()

        print(f"\nRequesting login code for {phone} ...")
        sent = await client.send_code_request(phone)
        print("Login code sent to Telegram (check your phone).\n")

        code = input("Enter the 6-digit code from Telegram: ").strip()

        try:
            await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
            print("\nSUCCESS! Logged in.")
        except Exception as error:
            print("\nLogin failed:", error)

            if "SessionPasswordNeeded" in type(error).__name__:
                pwd = input("Enter your 2FA password: ")
                await client.sign_in(password=pwd)
                print("\nSUCCESS! Logged in (2FA).")

    else:
        print("\nAlready logged in - no action needed.")

    me = await client.get_me()
    print("Logged in as:", me.username or me.first_name, f"({me.phone})")

    print("\nNow run:  venv/bin/python run.py")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())