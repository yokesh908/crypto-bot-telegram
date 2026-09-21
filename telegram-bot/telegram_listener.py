import os
import asyncio
from datetime import datetime, timezone
from telethon import TelegramClient, events


class TelegramSignalListener:
    """
    Listens to a Telegram channel/group for signal messages.

    Uses Telethon (user account) so it can read any channel the
    configured phone number has joined.
    """

    def __init__(self, signal_callback=None):
        self.api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
        self.api_hash = os.getenv("TELEGRAM_API_HASH", "")
        self.channel = os.getenv("TELEGRAM_CHANNEL", "")

        # support comma-separated channels
        self.channels = [
            c.strip()
            for c in self.channel.split(",")
            if c.strip()
        ]

        self.session_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "telegram_session",
        )

        self.callback = signal_callback

        # Cloud-friendly: prefer a StringSession stored in TELEGRAM_STRING_SESSION
        # env var. Falls back to the file-based session for local runs.
        string_session = os.getenv("TELEGRAM_STRING_SESSION", "").strip()
        if string_session:
            from telethon.sessions import StringSession

            self.client = TelegramClient(
                StringSession(string_session),
                self.api_id,
                self.api_hash,
            )
        else:
            self.client = TelegramClient(
                self.session_file,
                self.api_id,
                self.api_hash,
            )

    async def _handle_message(self, event):
        message = event.message

        if not message or not message.text:
            return

        text = message.text.strip()

        if not text:
            return

        # channel name for per-channel P&L tracking
        try:
            chat = await event.get_chat()
            channel_name = getattr(chat, "title", None) or str(event.chat_id)
        except Exception:
            channel_name = str(event.chat_id)

        print()
        print("=" * 70)
        print("[TELEGRAM] New message received")
        print(f"Chat   : {event.chat_id} ({channel_name})")
        print(f"Time   : {message.date}")
        print(f"Message: {text}")
        print("=" * 70)

        # Telethon 1.x AWAITS every handler, so this must be async even
        # though the work itself is handed to a task below.

        # After a crash/restart Telegram replays missed updates. A call
        # that is already minutes old must NOT be traded - the premium
        # has moved and the levels are no longer valid.
        max_age_min = float(os.getenv("SIGNAL_MAX_AGE_MINUTES", "10"))
        if max_age_min > 0 and message.date is not None:
            age_min = (
                datetime.now(timezone.utc)
                - message.date.astimezone(timezone.utc)
            ).total_seconds() / 60

            if age_min > max_age_min:
                print(f"[TELEGRAM][STALE] {age_min:.0f} min old "
                      f"(limit {max_age_min:.0f}) - not trading it")
                return

        if self.callback:
            asyncio.create_task(self._safe_call(text, channel_name))

    async def _safe_call(self, text, channel=""):
        try:
            try:
                self.callback(text, channel)
            except TypeError:
                # backward compat: callbacks taking only text
                self.callback(text)
        except Exception as error:
            print("[TELEGRAM][ERROR] Callback failed:", error)

    async def _drain_history(self):
        """Reads last N messages from the channel so signals sent
        while offline are not missed."""
        history_limit = int(os.getenv("HISTORY_BACKFILL", "3"))

        if history_limit <= 0 or not self.channels:
            return

        for channel in self.channels:
            try:
                entity = await self._resolve_one(channel)

                print(
                    f"[TELEGRAM] Backfilling last {history_limit} messages "
                    f"from '{channel}' ..."
                )

                async for message in self.client.iter_messages(
                    entity,
                    limit=history_limit,
                ):
                    if message and message.text and message.text.strip():
                        text = message.text.strip()

                        print()
                        print("[TELEGRAM][HISTORY]")
                        print(f"  Time   : {message.date}")
                        print(f"  Message: {text}")

                        if self.callback:
                            import threading

                            threading.Thread(
                                target=self._safe_history_callback,
                                args=(text, str(channel)),
                                daemon=True,
                            ).start()

            except Exception as error:
                print(f"[TELEGRAM][HISTORY] ({channel}) error:", error)

    def _safe_history_callback(self, text, channel=""):
        """History backfill runs in a threads; guard it so one bad message
        does not take down the listener."""
        try:
            if self.callback:
                try:
                    self.callback(text, channel)
                except TypeError:
                    self.callback(text)
        except Exception as error:
            print(f"[TELEGRAM][HISTORY ERROR] {error}")

    async def _resolve_one(self, channel):
        """Resolve a single channel by ID, @username, OR exact dialog name."""
        try:
            entity = await self.client.get_entity(channel)
            return entity
        except Exception:
            pass

        # numeric channel ID (e.g. -1004439654911)
        digits = channel.lstrip("-")
        if channel.lstrip("+-").isdigit():
            try:
                entity = await self.client.get_entity(int(channel))
                print("[TELEGRAM] Resolved channel ID:", channel)
                return entity
            except Exception:
                pass

        query = channel.lstrip("@").strip().lower()

        async for dialog in self.client.iter_dialogs():
            dialog_name = (dialog.name or "").strip().lower()

            if dialog_name == query or query in dialog_name:
                print("[TELEGRAM] Found channel via dialog search:",
                      dialog.name)
                return dialog.entity

        raise ValueError(
            f"Could not find channel '{channel}'. Make sure your "
            "account is a member of this group."
        )

    async def start(self):
        if not self.api_id or not self.api_hash:
            raise ValueError(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env "
                "(get from https://my.telegram.org/apps)."
            )

        max_conflict_delay = float(os.getenv("SESSION_CONFLICT_DELAY_SEC", "300"))

        for attempt in range(5):
            try:
                await self.client.start()
                break
            except Exception as error:
                err_str = str(error).lower()
                if "two different ip" in err_str or "authorization key" in err_str:
                    print(f"[TELEGRAM] Session conflict detected. "
                          f"Waiting {max_conflict_delay:.0f}s before retry {attempt+1}/5...")
                    await asyncio.sleep(max_conflict_delay)
                    continue
                raise

        me = await self.client.get_me()

        print("[TELEGRAM] Logged in as:", me.username or me.first_name)
        print("[TELEGRAM] Listening for signals ...")

        if not self.channels:
            raise ValueError(
                "TELEGRAM_CHANNEL must be set in .env "
                "(e.g. @your_signal_channel or comma-separated list)"
            )

        # A fresh StringSession has an EMPTY entity cache: numeric channel
        # IDs (e.g. -1004439654911) cannot be resolved until the account's
        # dialogs have been fetched at least once. Warm the cache first,
        # then resolve channels, skipping any that fail so one stale ID
        # cannot crash-loop the whole bot.
        try:
            print("[TELEGRAM] Warming session entity cache (dialogs) ...")
            count = 0
            async for _dialog in self.client.iter_dialogs():
                count += 1
            print(f"[TELEGRAM] Session cache warmed ({count} dialogs).")
        except Exception as error:
            print("[TELEGRAM][WARN] Could not warm dialog cache:", error)

        await self._drain_history()

        resolved = 0
        for channel in self.channels:
            try:
                entity = await self._resolve_one(channel)
            except Exception as error:
                print(
                    f"[TELEGRAM][WARN] Could not resolve channel "
                    f"'{channel}' - skipping. ({error})"
                )
                continue

            self.client.add_event_handler(
                self._handle_message,
                events.NewMessage(chats=entity),
            )
            resolved += 1
            print("[TELEGRAM] Monitoring channel:", channel)

        if resolved == 0:
            raise ValueError(
                "None of the TELEGRAM_CHANNEL entries could be resolved. "
                "Make sure this account is a member of those channels and "
                "the IDs/usernames are correct."
            )

        print("[TELEGRAM] Waiting for signals ... Press Ctrl+C to stop.")

        return await self.client.run_until_disconnected()