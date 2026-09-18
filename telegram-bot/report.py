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

    today = datetime.now().date().isoformat()
    today_rows = [
        r for r in rows
        if (r.get("time") or "")[:10] == today
    ]
    today_pnl = sum(float(r["pnl"] or 0) for r in today_rows)

    print("=" * 60)
    print("PAPER TRADING REPORT")
    print("=" * 60)
    print(f"Today        : {today} "
          f"({len(today_rows)} trades, PnL {today_pnl:,.2f} INR)")
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

    # Per channel: which channels feed profits vs losses, ranked by
    # expectancy = (avg win x win rate) - (avg loss x loss rate).
    # Expectancy is the average INR you expect PER TRADE from this channel.
    print()
    print("Per channel (ranked by expectancy, best first):")
    by_channel = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0,
                                      "win_sum": 0.0, "loss_sum": 0.0})
    for r in rows:
        ch = (r.get("channel") or "").strip() or "-"
        pnl = float(r["pnl"] or 0)
        d = by_channel[ch]
        d["n"] += 1
        d["pnl"] += pnl
        if pnl > 0:
            d["wins"] += 1
            d["win_sum"] += pnl
        elif pnl < 0:
            d["loss_sum"] += pnl  # negative

    def _expectancy(d):
        n, w = d["n"], d["wins"]
        if n == 0:
            return 0.0
        win_rate = w / n
        avg_win = d["win_sum"] / w if w else 0.0
        losses = n - w
        avg_loss = abs(d["loss_sum"] / losses) if losses else 0.0
        return avg_win * win_rate - avg_loss * (1 - win_rate)

    for ch in sorted(by_channel, key=lambda c: _expectancy(by_channel[c]),
                     reverse=True):
        d = by_channel[ch]
        n, w = d["n"], d["wins"]
        exp = _expectancy(d)
        print(
            f"  {ch:<30}: {n} trades, {w}/{n} wins "
            f"({(w / n * 100) if n else 0:.0f}%), "
            f"PnL {d['pnl']:,.2f}, "
            f"expectancy {exp:>+9,.2f} INR/trade"
            f"{'  [UNPROFITABLE - consider muting]' if exp < 0 else ''}"
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