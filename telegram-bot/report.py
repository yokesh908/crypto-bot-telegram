"""
Paper trading report.

Summarizes trades_log.csv: count, win rate, total PnL per day.

Run:  venv/bin/python report.py
"""
import csv
import os
from collections import defaultdict
from datetime import datetime


def main():
    log_file = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "trades_log.csv",
    )

    if not os.path.exists(log_file):
        print("No trades yet. trades_log.csv does not exist.")
        return

    with open(log_file) as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("No trades yet.")
        return

    total = len(rows)
    wins = [r for r in rows if (float(r["pnl"] or 0) > 0)]
    losses = [r for r in rows if (float(r["pnl"] or 0) < 0)]
    total_pnl = sum(float(r["pnl"] or 0) for r in rows)

    win_rate = len(wins) / total * 100

    print("=" * 60)
    print("PAPER TRADING REPORT")
    print("=" * 60)
    print(f"Total trades : {total}")
    print(f"Wins         : {len(wins)}")
    print(f"Losses       : {len(losses)}")
    print(f"Win rate     : {win_rate:.1f}%")
    print(f"Total PnL    : {total_pnl:,.2f} INR")

    print()
    print("Per day:")
    by_day = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    for r in rows:
        day = r["time"][:10]
        by_day[day]["n"] += 1
        by_day[day]["pnl"] += float(r["pnl"] or 0)
        if float(r["pnl"] or 0) > 0:
            by_day[day]["wins"] += 1

    for day in sorted(by_day.keys()):
        d = by_day[day]
        print(
            f"  {day}: {d['n']} trades, "
            f"{d['wins']}/{d['n']} wins, "
            f"PnL {d['pnl']:,.2f} INR"
        )

    print()
    print("Latest trades:")
    for r in rows[-8:]:
        print(
            f"  {r['time'][11:19]} | {r['symbol']:<20} | "
            f"{r['direction']:<4} | {r['exit_reason']:<12} | "
            f"{float(r['pnl'] or 0):>12,.2f}"
        )


if __name__ == "__main__":
    main()