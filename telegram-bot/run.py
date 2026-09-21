import asyncio
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

from telegram_listener import TelegramSignalListener
from exchange_connector import PaperExchange
from signal_executor import SignalExecutor, acquire_single_instance_lock


def build_exchange():
    provider = os.getenv("EXCHANGE_PROVIDER", "paper").strip().lower()

    if provider == "paper":
        return PaperExchange()

    if provider == "zerodha":
        from zerodha_exchange import ZerodhaExchange

        return ZerodhaExchange()

    raise ValueError(
        f"EXCHANGE_PROVIDER='{provider}' not supported yet. "
        "Use 'paper' or 'zerodha'."
    )


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, format, *args):
            return

    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


def main():
    threading.Thread(target=start_health_server, daemon=True).start()

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

    print("=" * 70)
    print("TELEGRAM SIGNAL -> AUTO TRADE BOT")
    print("Mode: " + os.getenv("MODE", "paper").upper())
    print("=" * 70)

    if not os.getenv("TELEGRAM_API_ID") or not os.getenv("TELEGRAM_API_HASH"):
        print()
        print("ERROR: Missing Telegram credentials.")
        print("1. Go to https://my.telegram.org/apps")
        print("2. Create an app")
        print("3. Copy API ID and API HASH into your .env file")
        print("4. Rename .env.example to .env and fill it")
        print()
        return

# ------------------------------------------------------------------
# Watchdog: if the client drops, restart the listener instead of
# exiting.  This prevents 7-day gaps like Sep 10-14 where the bot was
# never running.  The systemd restart loop will still catch a full
# process death, but this keeps the *listener* alive through transient
# disconnects (the "ConnectionError: Connection to Telegram failed N
# time(s)" path in Telethon).
# ------------------------------------------------------------------
    max_restarts = int(os.getenv("BOT_MAX_RESTARTS", "10"))
    restart_delay = float(os.getenv("BOT_RESTART_DELAY_SEC", "15"))
    restarts = 0
    backoff = restart_delay

    while True:
        try:
            if not acquire_single_instance_lock(
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot.pid")
            ):
                print("[WATCHDOG] Another instance is running. Exiting.")
                return
            run_once()
            break
        except KeyboardInterrupt:
            print("\nBot stopped.")
            break
        except Exception as error:
            restarts += 1
            err_str = str(error)
            if "two different IP addresses" in err_str or "authorization key" in err_str:
                print()
                print("=" * 70)
                print("[WATCHDOG] SESSION CONFLICT detected!")
                print("           The same Telegram session is being used")
                print("           from another device/IP. Regenerate session.")
                print("=" * 70)
                delay = float(os.getenv("SESSION_CONFLICT_DELAY_SEC", "300"))
                print(f"[WATCHDOG] Waiting {delay:.0f}s before retry...")
                time.sleep(delay)
                backoff = restart_delay
            elif restarts >= max_restarts:
                print()
                print("=" * 70)
                print(f"[WATCHDOG] Max restarts ({max_restarts}) reached. Giving up.")
                print("=" * 70)
                sys.exit(1)
            else:
                print()
                print("=" * 70)
                print(f"[WATCHDOG] Bot run failed (attempt {restarts}/{max_restarts})")
                print(f"           {error}")
                print("=" * 70)
                print(f"[WATCHDOG] Restarting in {backoff:.0f}s ...")
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)


def run_once():
    exchange = build_exchange()
    exchange.connect()

    executor = SignalExecutor(exchange=exchange)

    listener = TelegramSignalListener(
        signal_callback=executor.on_signal_text,
    )

    asyncio.run(listener.start())


if __name__ == "__main__":
    main()
