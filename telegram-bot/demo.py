"""
Demo mode: simulates Telegram signals arriving, without needing
your real Telegram or exchange credentials.

Run:  venv/bin/python demo.py
"""
import time

from signal_parser import parse_signal
from exchange_connector import PaperExchange
from signal_executor import SignalExecutor

DEMO_SIGNALS = [
    "nfty 23150pe buy above 60 target 1 70 target 2 80 target 3 90 stoploss 50",
    "ETHUSDT long above 3500 target1 3650 target2 3800 target3 4000 stoploss 3300",
    "NIFTY 24500 PE BUY 150 T1 165 T2 180 T3 200 SL 135",
    "reliance buy 2400 sl 2350 target1 2500 target2 2600",
]


def main():
    print("=" * 70)
    print("DEMO MODE - simulated Telegram signals (paper trade)")
    print("=" * 70)

    exchange = PaperExchange()
    exchange.connect()

    executor = SignalExecutor(exchange=exchange)

    input("Press ENTER to start simulating signals ...\n")

    for text in DEMO_SIGNALS:
        print()
        print("[DEMO] Injecting signal:")
        print("  ", text)

        executor.on_signal_text(text)

        time.sleep(3)

    print()
    print("=" * 70)
    print("Demo finished. Check trades_log.csv for results.")
    print("=" * 70)


if __name__ == "__main__":
    main()