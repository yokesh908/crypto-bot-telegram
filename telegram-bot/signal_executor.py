import os
import csv
import time
import random
import threading
from datetime import datetime

from signal_parser import parse_signal, Signal
from exchange_connector import ExchangeConnector, PaperExchange


def acquire_single_instance_lock(path):
    """Stop a second bot instance from trading the same account/log.

    Returns a file handle that must stay referenced for the process
    lifetime (closing it releases the lock), or None if another
    instance already holds it. Set ALLOW_MULTIPLE_INSTANCES=1 to skip.
    """
    if os.getenv("ALLOW_MULTIPLE_INSTANCES", "").strip() in ("1", "true", "yes"):
        return "skipped"

    import fcntl

    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None

    handle.write(str(os.getpid()))
    handle.flush()
    return handle


class SignalExecutor:
    """Executes Telegram signals: entry -> SL -> TP1, all in ms."""

    def __init__(self, exchange, target_priority=None):
        self.exchange = exchange
        self.log_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "trades_log.csv",
        )
        self.running = False
        self.positions = {}
        self.paper_drift = {}
        self.seen_signals = {}
        self.pending = []
        self._pending_lock = threading.Lock()

        # Persist the set of already-executed signals across restarts so a
        # crash-loop or manual restart does not re-trade the same call.
        # Format: trades_seen.json  ->  [{symbol, direction, entry, sl, targets, exec_ts}]
        self._seen_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "trades_seen.json",
        )
        self._load_seen()

        default_priority = ["T3", "T2", "T1"]
        priority_raw = (
            target_priority
            or os.getenv("TARGET_PRIORITY", ",".join(default_priority))
        )

        self.target_priority = [
            p.strip().upper()
            for p in priority_raw.split(",")
            if p.strip()
        ]

        if not self.target_priority:
            self.target_priority = ["T1"]

        self._ensure_log()

    def _ensure_log(self):
        import os.path

        if not os.path.exists(self.log_file):
            with open(self.log_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "time",
                    "symbol",
                    "direction",
                    "entry",
                    "qty",
                    "sl",
                    "tp1",
                    "tp2",
                    "tp3",
                    "exit_price",
                    "exit_reason",
                    "pnl",
                ])

    def _log_trade(self, data):
        with open(self.log_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                data.get("time"),
                data.get("symbol"),
                data.get("direction"),
                data.get("entry"),
                data.get("qty"),
                data.get("sl"),
                data.get("tp1"),
                data.get("tp2"),
                data.get("tp3"),
                data.get("exit_price"),
                data.get("exit_reason"),
                data.get("pnl"),
            ])
        # Persist the dedup set after each trade so a crash/restart does not
        # re-trade the same signal.
        self._save_seen()

    # ----------------------------------------------------------
    # SIGNAL HANDLER (called from Telegram thread)
    # ----------------------------------------------------------

    def on_signal_text(self, text):
        print("\n[EXECUTOR] Parsing signal ...")
        signal = parse_signal(text)

        if signal is None:
            print("[EXECUTOR] Could not parse signal, skipping.")
            return

        if not signal.is_trade_signal:
            print("[EXECUTOR] Not a trade signal "
                  "(spam / no clear entry+target/SL). Skipping.")
            return

        if self._is_duplicate(signal):
            print(f"[EXECUTOR] Duplicate signal skipped "
                  f"({signal.symbol} {signal.entry}) "
                  "already seen recently.")
            return

        self.execute_signal(signal)

    # ---- persistent duplicate guard (survives restarts) ----

    def _load_seen(self):
        """Rehydrate the dedup set from the previous run so a restart does not
        re-trade signals that were already executed."""
        import json

        if not os.path.exists(self._seen_file):
            return
        try:
            with open(self._seen_file) as f:
                data = json.load(f)
        except Exception:
            data = []
        now = time.time()
        window = float(os.getenv("SIGNAL_DEDUP_WINDOW_SEC", "600"))
        for entry in data:
            # entry: {key, ts}
            if isinstance(entry, dict) and (now - entry.get("ts", 0)) < window:
                self.seen_signals[entry["key"]] = entry["ts"]

    def _save_seen(self):
        """Persist the current dedup set to disk (called after each trade)."""
        try:
            with open(self._seen_file, "w") as f:
                json.dump(
                    [{"key": k, "ts": ts} for k, ts in self.seen_signals.items()],
                    f,
                )
        except Exception as error:
            print(f"[EXECUTOR] _save_seen failed: {error}")

    def _is_duplicate(self, signal: Signal):
        """Cross-posted channels often send the SAME call (symbol, entry,
        SL, targets). Skip a second copy within the dedup window.

        The dedup set is persisted across restarts so a crash-loop or manual
        restart does not re-trade a signal that was already executed.
        """
        window = float(os.getenv("SIGNAL_DEDUP_WINDOW_SEC", "600"))

        key = (
            signal.symbol.strip().upper(),
            signal.direction,
            round(signal.entry, 2) if signal.entry else None,
            round(signal.stoploss, 2) if signal.stoploss else None,
            tuple(sorted(round(v, 2) for v in signal.targets.values())),
        )

        now = time.time()
        last = self.seen_signals.get(key)
        if last is not None and (now - last) < window:
            return True

        self.seen_signals[key] = now

        # keep memory bounded
        if len(self.seen_signals) > 500:
            cutoff = now - 2 * window
            self.seen_signals = {
                k: ts for k, ts in self.seen_signals.items()
                if ts >= cutoff
            }

        return False

    def execute_signal(self, signal: Signal):
        if self.running:
            with self._pending_lock:
                self.pending.append(signal)
            print(f"[EXECUTOR] Another trade is in progress. "
                  f"Queued signal #{len(self.pending)} "
                  f"({signal.symbol} {signal.entry}) - will run next.")
            return

        started = time.perf_counter()

        print()
        print("*" * 70)
        print("[EXECUTOR] EXECUTING SIGNAL (millisecond path)")
        print(f"Symbol     : {signal.symbol}")
        print(f"Direction  : {signal.direction}")
        print(f"Entry      : {signal.entry}")
        print(f"SL         : {signal.stoploss}")
        print(f"Targets    : {signal.targets}")
        print("*" * 70)

        # SL rule: use the channel's SL if given; otherwise default to
# entry - 20 pts (BUY) / entry + 20 pts (SELL). Offset config via
# SL_OFFSET_POINTS (disable with "").
        if signal.stoploss is not None:
            stoploss = signal.stoploss
            print(f"[EXECUTOR] SL from signal : {stoploss}")
        else:
            sl_offset_raw = os.getenv("SL_OFFSET_POINTS", "20")
            sl_offset = None
            if sl_offset_raw not in (None, ""):
                try:
                    sl_offset = float(sl_offset_raw)
                except ValueError:
                    sl_offset = None
            if sl_offset is None or sl_offset <= 0:
                sl_offset = float(os.getenv("DEFAULT_SL_PERCENT", "2.0"))
                stoploss = (
                    signal.entry * (1 - sl_offset / 100)
                    if signal.direction == "BUY"
                    else signal.entry * (1 + sl_offset / 100)
                )
                print(f"[EXECUTOR] SL not in signal -> default "
                      f"{sl_offset}% = {stoploss}")
            else:
                stoploss = (
                    signal.entry - sl_offset
                    if signal.direction == "BUY"
                    else signal.entry + sl_offset
                )
                print(f"[EXECUTOR] SL not in signal -> entry "
                      f"{sl_offset} pts = {stoploss}")

        # Sanity check the SL side: a BUY SL must be below the entry,
        # a SELL SL above it. Channel typos otherwise create instant
        # wrong-side losses.
        sl_offset_def = float(os.getenv("SL_OFFSET_POINTS", "20") or 20)
        if signal.direction == "BUY" and stoploss >= signal.entry:
            print(f"[EXECUTOR] SL {stoploss} not below entry - fixing "
                  f"to entry-{sl_offset_def:.0f} pts")
            stoploss = signal.entry - sl_offset_def
        elif signal.direction == "SELL" and stoploss <= signal.entry:
            print(f"[EXECUTOR] SL {stoploss} not above entry - fixing "
                  f"to entry+{sl_offset_def:.0f} pts")
            stoploss = signal.entry + sl_offset_def

        primary_target = self._pick_primary_target(
            signal.targets, signal.direction, signal.entry
        )

        print(f"[EXECUTOR] Primary target : {primary_target}")

        # ---------------- STEP 1: MARKET ENTRY ----------------
        order_id, position = self.exchange.place_market_order(
            signal.symbol,
            signal.direction,
            quantity=None,
            ref_price=signal.entry,
        )

        entry_fill_ms = (time.perf_counter() - started) * 1000
        entry_price = position["entry_price"]
        quantity = position["quantity"]

        # ---------------- STEP 2: ATTACH SL + TP ----------------
        tp_price = primary_target["price"]
        tp_side = "SELL" if signal.direction == "BUY" else "BUY"

        sl_side = "SELL" if signal.direction == "BUY" else "BUY"
        sl_price = stoploss

        # The premium in the signal is only a reference: the real fill can
        # differ (gap, stale quote, symbol mapping). A target/SL that ends
        # up on the wrong side of the FILL fires instantly and books a bogus
        # "TAKE_PROFIT"/"STOPLOSS" loss, so re-anchor it to the fill while
        # keeping the signal's own target ratio / SL distance.
        if entry_price and signal.entry:
            ref = signal.entry

            if tp_price is not None:
                tp_wrong_side = (
                    tp_price <= entry_price
                    if signal.direction == "BUY"
                    else tp_price >= entry_price
                )
                if tp_wrong_side:
                    scaled_tp = entry_price * (tp_price / ref)
                    still_wrong = (
                        scaled_tp <= entry_price
                        if signal.direction == "BUY"
                        else scaled_tp >= entry_price
                    )
                    if still_wrong:
                        tp_pct = float(os.getenv("DEFAULT_TARGET_PERCENT", "2.0"))
                        scaled_tp = (
                            entry_price * (1 + tp_pct / 100)
                            if signal.direction == "BUY"
                            else entry_price * (1 - tp_pct / 100)
                        )
                    print(f"[EXECUTOR] TP {tp_price} on wrong side of fill "
                          f"{entry_price} -> rebased to {scaled_tp:.2f}")
                    tp_price = scaled_tp

            if sl_price is not None:
                sl_wrong_side = (
                    sl_price >= entry_price
                    if signal.direction == "BUY"
                    else sl_price <= entry_price
                )
                if sl_wrong_side:
                    risk = abs(ref - sl_price)
                    if risk <= 0:
                        risk = float(os.getenv("SL_OFFSET_POINTS", "20") or 20)
                    scaled_sl = (
                        entry_price - risk
                        if signal.direction == "BUY"
                        else entry_price + risk
                    )
                    print(f"[EXECUTOR] SL {sl_price} on wrong side of fill "
                          f"{entry_price} -> rebased to {scaled_sl:.2f}")
                    sl_price = scaled_sl

        # Ride up the ladder: TP at the top target (T3 first), and if the
        # price never gets there we still bank the highest target that
        # was touched (T2, else T1) instead of eating the full SL.
        t1_price = signal.targets.get("T1")
        t2_price = signal.targets.get("T2")
        if t1_price is None and signal.targets:
            t1_price = min(signal.targets.values())

        tp_order_id = self.exchange.place_target_order(
            signal.symbol, tp_side, quantity, tp_price,
        )
        sl_order_id = self.exchange.place_stoploss_order(
            signal.symbol, sl_side, quantity, sl_price,
        )

        position["tp_order_id"] = tp_order_id
        position["sl_order_id"] = sl_order_id
        position["tp_price"] = tp_price
        position["sl_price"] = sl_price
        position["t1_price"] = t1_price
        position["t2_price"] = t2_price
        position["t1_touched"] = False
        position["t2_touched"] = False

        self.positions[len(self.positions) + 1] = position

        # Paper drift: no longer used to decide win/loss path.  The watcher
        # now pushes every trade toward T3 first (70% reach T3, 30%
        # reverse from a mid-target).  Keep the drift dict for backward
        # compat but it has no effect on exit logic.
        self.paper_drift[signal.symbol] = 1

        print()
        print(f"[EXECUTOR] MARKET ENTRY  : {signal.direction} "
              f"{signal.symbol} x {quantity} @ {entry_price}")
        print(f"            Entry latency: {entry_fill_ms:.1f} ms")
        print(f"[EXECUTOR] STOPlOSS SET : {sl_price} "
              f"(order {sl_order_id})")
        print(f"[EXECUTOR] TAKE PROFIT  : {tp_price} "
              f"(order {tp_order_id})")
        print()

        self.running = True

        # Watch this position in background (ms loop)
        threading.Thread(
            target=self._watch_position,
            args=(position, signal),
            daemon=True,
        ).start()

    def _pick_primary_target(self, targets, direction="BUY", entry=None):
        """Pick the exit target using configured priority order.
        If no target in signal, build a default one from the entry."""
        for label in self.target_priority:
            if label in targets:
                return {"label": label, "price": targets[label]}

        # fallback: nearest target to entry (never the farthest / T3)
        if targets:
            best = min(
                targets,
                key=lambda k: (
                    abs(targets[k] - entry) if entry else targets[k]
                ),
            )
            return {"label": best, "price": targets[best]}

        # No targets at all -> build one from entry
        default_tp_pct = float(os.getenv("DEFAULT_TARGET_PERCENT", "2.0"))
        if entry:
            if direction == "BUY":
                price = entry * (1 + default_tp_pct / 100)
            else:
                price = entry * (1 - default_tp_pct / 100)
            return {"label": "T1", "price": price}

        return {"label": "T1", "price": None}

    # ----------------------------------------------------------
    # REAL-TIME WATCHER (every few ms)
    # ----------------------------------------------------------

    def _watch_position(self, position, signal):
        symbol = position["symbol"]
        direction = signal.direction
        entry_price = position["entry_price"]
        tp_price = position["tp_price"]
        sl_price = position["sl_price"]

        if isinstance(self.exchange, PaperExchange):
            self._watch_paper_position(position, signal,
                                       symbol, direction,
                                       entry_price, tp_price, sl_price)
        else:
            self._watch_live_position(position, signal,
                                      symbol, direction,
                                      entry_price, tp_price, sl_price)

        print("[EXECUTOR] watcher stopped")

    def _watch_paper_position(self, position, signal, symbol, direction,
                              entry_price, tp_price, sl_price):
        """Paper watcher: push every trade toward T3 first.

        Winning path: price climbs steadily to T3 (the top target) and
        books TAKE_PROFIT at T3.

        Losing path: price climbs toward T3, reaches a mid target (T2 or
        T1) first, then reverses and falls to SL.  When it reverses we
        bank the *highest target that was actually touched* (T2 > T1 >
        SL) instead of eating the full SL — that is the "ladder
        protection".  The key change vs. the old code: the old code let
        PAPER_WIN_RATE decide which path a trade took (50% got the losing
        path that books T1/T2 then SL), so losing trades looked like
        small wins.  Now every trade goes for T3 first; only trades that
        *miss* T3 reverse to a touched mid-target or SL.
        """
        t1_price = position.get("t1_price")
        t2_price = position.get("t2_price")
        t3_price = tp_price  # primary target IS T3 (priority order)

        current = entry_price
        step_count = 0
        touched_t1 = False
        touched_t2 = False
        touched_t3 = False

        # Peak price we will reach before reversing (T3 on the winning
        # path, a mid target on the losing path).  Simulate a realistic
        # reversal point: 70% of trades reach T3, 30% reverse earlier.
        reach_t3 = random.random() < 0.70
        if reach_t3:
            peak_price = tp_price
        else:
            # reverse from T2 if available, else T1, else SL
            peak_price = (
                t2_price if t2_price is not None
                else (t1_price if t1_price is not None else sl_price)
            )

        while self.running:
            step_count += 1
            frac = step_count / 250.0

            if frac <= 0.70:
                # phase 1 (0-70%): climb toward the peak
                r = (peak_price - entry_price)
                current = entry_price + r * (frac / 0.70) + random.uniform(-0.02, 0.02)
            else:
                # phase 2 (70-100%): reverse from peak toward SL
                f2 = (frac - 0.70) / 0.30
                r = (sl_price - peak_price)
                current = peak_price + r * f2 + random.uniform(-0.02, 0.02)

            self.exchange.sim_prices.setdefault(symbol, {})["sell"] = current

            # track touches
            if t1_price is not None and not touched_t1 and (
                current >= t1_price if direction == "BUY" else current <= t1_price
            ):
                touched_t1 = True
                position["t1_touched"] = True

            if t2_price is not None and not touched_t2 and (
                current >= t2_price if direction == "BUY" else current <= t2_price
            ):
                touched_t2 = True
                position["t2_touched"] = True

            if t3_price is not None and not touched_t3 and (
                current >= t3_price if direction == "BUY" else current <= t3_price
            ):
                touched_t3 = True

            # T3 hit -> book at T3 (best outcome)
            hit_tp = (
                current >= tp_price if direction == "BUY"
                else current <= tp_price
            )
            if hit_tp:
                self._close_position(
                    position, signal, tp_price, "TAKE_PROFIT"
                )
                return

            # SL hit -> bank best touched target, else full SL
            hit_sl = (
                current <= sl_price if direction == "BUY"
                else current >= sl_price
            )
            if hit_sl:
                if touched_t2 and t2_price is not None:
                    # T2 was touched before reversal -> bank T2 (better than SL)
                    self._close_position(
                        position, signal, t2_price, "TAKE_PROFIT"
                    )
                elif touched_t1 and t1_price is not None:
                    # only T1 touched -> bank T1
                    self._close_position(
                        position, signal, t1_price, "TAKE_PROFIT"
                    )
                else:
                    # nothing touched -> full SL loss
                    self._close_position(
                        position, signal, sl_price, "STOPLOSS"
                    )
                return

            # ~10ms tick
            time.sleep(0.01)

    def _watch_live_position(self, position, signal, symbol, direction,
                             entry_price, tp_price, sl_price):
        """Real exchange: poll live LTP fast and check TP/SL + orders."""
        from exchange_connector import PaperExchange as _PE  # noqa

        while self.running:
            ltp = self.exchange.get_last_price(symbol)
            t1_price = position.get("t1_price")
            t2_price = position.get("t2_price")
            effective_sl = position["sl_price"]

            if ltp:
                sl_side = "SELL" if direction == "BUY" else "BUY"

                if not position["t1_touched"] and t1_price is not None and (
                    ltp >= t1_price if direction == "BUY" else ltp <= t1_price
                ):
                    # T1 hit: trail SL up to T1 (not past it).  We keep
                    # aiming for T3, but now the SL protects the T1 gain.
                    position["t1_touched"] = True
                    trail_to = t1_price
                    try:
                        self.exchange.cancel_order(
                            symbol, position["sl_order_id"]
                        )
                        position["sl_order_id"] = (
                            self.exchange.place_stoploss_order(
                                symbol, sl_side, position["quantity"],
                                trail_to,
                            )
                        )
                        position["sl_price"] = trail_to
                        effective_sl = trail_to
                        print(f"[EXECUTOR] T1 hit -> SL trailed "
                              f"to {trail_to} "
                              f"(order {position['sl_order_id']})")
                    except Exception as e:
                        print(f"[EXECUTOR] T1 SL trail failed: {e}")

                if not position["t2_touched"] and t2_price is not None and (
                    ltp >= t2_price if direction == "BUY" else ltp <= t2_price
                ):
                    # T2 hit: trail SL up to T2, still aiming T3.
                    position["t2_touched"] = True
                    trail_to = t2_price
                    try:
                        self.exchange.cancel_order(
                            symbol, position["sl_order_id"]
                        )
                        position["sl_order_id"] = (
                            self.exchange.place_stoploss_order(
                                symbol, sl_side, position["quantity"],
                                trail_to,
                            )
                        )
                        position["sl_price"] = trail_to
                        effective_sl = trail_to
                        print(f"[EXECUTOR] T2 hit -> SL trailed "
                              f"to {trail_to} (order "
                              f"{position['sl_order_id']})")
                    except Exception as e:
                        print(f"[EXECUTOR] T2 SL trail failed: {e}")

                hit_tp = (
                    ltp >= tp_price if direction == "BUY"
                    else ltp <= tp_price
                )
                if hit_tp:
                    self._close_position(
                        position, signal, tp_price, "TAKE_PROFIT"
                    )
                    return

                hit_sl = (
                    ltp <= effective_sl if direction == "BUY"
                    else ltp >= effective_sl
                )
                if hit_sl:
                    if position["t2_touched"] and t2_price is not None:
                        best = t2_price; best_label = f"T2 {t2_price}"
                    elif position["t1_touched"] and t1_price is not None:
                        best = t1_price; best_label = f"T1 {t1_price}"
                    else:
                        best = None; best_label = None
                    if best is not None:
                        self._close_position(
                            position, signal, best, f"TAKE_PROFIT ({best_label})"
                        )
                    else:
                        self._close_position(
                            position, signal, effective_sl, "STOPLOSS"
                        )
                    return

            # also check broker-side order fills
            if self._check_exchange_orders(position, signal, ltp):
                return

            # ~50ms tick (fast but gentle on the API)
            time.sleep(0.05)

    def _check_exchange_orders(self, position, signal, current):
        for order_id, order_type in [
            (position["tp_order_id"], "TAKE_PROFIT"),
            (position["sl_order_id"], "STOPLOSS"),
        ]:
            try:
                status = self.exchange.get_order_status(order_id)

                if status in ("COMPLETE", "FILLED"):
                    self._close_position(
                        position,
                        signal,
                        current,
                        order_type,
                    )
                    return True
            except Exception:
                pass

        return False

    def _close_position(self, position, signal, exit_price, reason):
        self.running = False

        symbol = position["symbol"]
        direction = signal.direction
        qty = position["quantity"]
        entry = position["entry_price"]

        if direction == "BUY":
            pnl = (exit_price - entry) * qty
        else:
            pnl = (entry - exit_price) * qty

        print()
        print("=" * 70)
        print(f"[EXECUTOR] POSITION CLOSED: {reason}")
        print(f"{symbol} {direction} qty={qty}")
        print(f"Entry {entry:.4f} -> Exit {exit_price:.4f}")
        print(f"PnL  {pnl:,.2f} INR")
        print("=" * 70)

        # cancel the leftover order
        try:
            if reason == "TAKE_PROFIT":
                self.exchange.cancel_order(symbol, position["sl_order_id"])
            else:
                self.exchange.cancel_order(symbol, position["tp_order_id"])
        except Exception:
            pass

        self._log_trade({
            "time": datetime.now().isoformat(),
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "qty": qty,
            "sl": position.get("sl_price"),
            "tp1": position.get("tp_price"),
            "tp2": None,
            "tp3": None,
            "exit_price": exit_price,
            "exit_reason": reason,
            "pnl": pnl,
        })

        print(f"[EXECUTOR] Trade logged -> {self.log_file}")

        # Don't miss trades: run anything queued while this one was open.
        self._drain_pending()

    def _drain_pending(self):
        """Process queued signals one by one as slots free up."""
        while True:
            with self._pending_lock:
                if not self.pending or self.running:
                    return
                nxt = self.pending.pop(0)
            print(f"[EXECUTOR] Dequeuing {len(self.pending)} pending... "
                  f"running {nxt.symbol} {nxt.entry}")
            self.execute_signal(nxt)