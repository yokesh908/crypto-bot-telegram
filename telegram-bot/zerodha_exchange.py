"""
Zerodha Kite Connect exchange connector.

Maps signal symbols like "NIFTY 23600 CE" to actual Kite instruments
and places real market / limit / SL orders.

Requires:
  KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN  (in .env)

Get access token with zerodha_login.py (one time). 2FA TOTP must be
disabled on your Zerodha account, or provide KITE_TOTP_SECRET.
"""
import os
import re
from datetime import datetime

from exchange_connector import ExchangeConnector


class ZerodhaExchange(ExchangeConnector):
    def __init__(self, trade_capital_inr=30000.0):
        super().__init__(trade_capital_inr)

        self.api_key = os.getenv("KITE_API_KEY", "")
        self.api_secret = os.getenv("KITE_API_SECRET", "")
        self.access_token = os.getenv("KITE_ACCESS_TOKEN", "")

        self.kite = None
        self.instruments = {}
        self.instruments_by_symbol = {}

    # ----------------------------------------------------------
    # CONNECTION
    # ----------------------------------------------------------

    def connect(self):
        if not self.api_key or not self.access_token:
            raise ValueError(
                "KITE_API_KEY and KITE_ACCESS_TOKEN must be set in .env. "
                "Run zerodha_login.py once to generate the access token."
            )

        from kiteconnect import KiteConnect

        self.kite = KiteConnect(api_key=self.api_key)
        self.kite.set_access_token(self.access_token)

        profile = self.kite.profile()
        print("[ZERODHA] Connected as:",
              profile.get("user_name", "unknown"))

        self._load_instruments()
        return True

    def _load_instruments(self):
        """Loads the instrument master so signal symbols can be resolved."""
        try:
            rows = self.kite.instruments()

            for row in rows:
                key = f"{row['name']}|{row['strike']}|{row['instrument_type']}"
                self.instruments.setdefault(key, []).append(row)

                if row.get("tradingsymbol"):
                    self.instruments_by_symbol.setdefault(
                        row["tradingsymbol"], row
                    )

            print(f"[ZERODHA] Loaded {len(rows)} instruments")
        except Exception as error:
            print("[ZERODHA] Could not load instruments:", error)

    # ----------------------------------------------------------
    # SYMBOL MAPPING  ("NIFTY 23600 CE" -> Kite instrument)
    # ----------------------------------------------------------

    def resolve_instrument(self, symbol):
        """Resolves a signal symbol to the nearest-expiry Kite instrument."""
        symbol = symbol.upper()

        # already a Kite tradingsymbol? e.g. NIFTY23NOV23600CE
        if symbol in self.instruments_by_symbol:
            return self.instruments_by_symbol[symbol]

        match = re.match(r"(NIFTY|BANKNIFTY|FINNIFTY|SENSEX)\s*(\d{4,5})\s*(CE|PE)", symbol)

        if not match:
            raise ValueError(f"Cannot map symbol '{symbol}' to a Kite "
                             "instrument")

        index, strike, option_type = match.groups()

        strike = int(strike)
        instruments = self.instruments.get(
            f"{index}|{strike}|{option_type}", []
        )

        if not instruments:
            raise ValueError(
                f"No {index} {strike} {option_type} instrument found"
            )

        # pick nearest expiry >= today
        today = datetime.now().date()

        def expiry_date(inst):
            try:
                return datetime.fromisoformat(inst["expiry"]).date()
            except Exception:
                return "9999-99-99"

        valid = [
            i for i in instruments
            if str(expiry_date(i)) >= str(today)
        ]

        if valid:
            instruments = valid

        instruments.sort(key=expiry_date)
        return instruments[0]

    # ----------------------------------------------------------
    # PRICE
    # ----------------------------------------------------------

    def get_last_price(self, symbol):
        inst = self.resolve_instrument(symbol)

        try:
            quote = self.kite.quote([inst["instrument_token"]])
            data = quote[str(inst["instrument_token"])]
            return float(data.get("last_price", 0))
        except Exception as error:
            print("[ZERODHA][LTP] error:", error)
            return None

    # ----------------------------------------------------------
    # ORDER HELPERS
    # ----------------------------------------------------------

    def _field(self, inst):
        return str(inst["instrument_token"])

    def place_market_order(self, symbol, side, quantity=None, ref_price=None):
        inst = self.resolve_instrument(symbol)

        if quantity is None:
            quantity = inst.get("lot_size", 1)

        quantity = int(quantity)

        order = self.kite.place_order(
            variety="regular",
            exchange=inst["exchange"],
            tradingsymbol=inst["tradingsymbol"],
            transaction_type=(
                "BUY" if side == "BUY" else "SELL"
            ),
            quantity=quantity,
            product="NRML",
            order_type="MARKET",
        )

        return order, {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "entry_price": ref_price,
            "order_id": order,
            "status": "OPEN",
        }

    def place_target_order(self, symbol, side, quantity, target_price):
        inst = self.resolve_instrument(symbol)
        quantity = max(int(quantity), 1)

        order = self.kite.place_order(
            variety="regular",
            exchange=inst["exchange"],
            tradingsymbol=inst["tradingsymbol"],
            transaction_type=(
                "BUY" if side == "BUY" else "SELL"
            ),
            quantity=quantity,
            product="NRML",
            order_type="LIMIT",
            price=target_price,
        )
        return order

    def place_stoploss_order(self, symbol, side, quantity, sl_price):
        inst = self.resolve_instrument(symbol)
        quantity = max(int(quantity), 1)

        order = self.kite.place_order(
            variety="regular",
            exchange=inst["exchange"],
            tradingsymbol=inst["tradingsymbol"],
            transaction_type=(
                "BUY" if side == "BUY" else "SELL"
            ),
            quantity=quantity,
            product="NRML",
            order_type="SL",
            price=sl_price,
            trigger_price=sl_price,
        )
        return order

    def cancel_order(self, symbol, order_id):
        if not order_id:
            return False
        try:
            return self.kite.cancel_order(
                variety="regular",
                order_id=order_id,
            )
        except Exception as error:
            print("[ZERODHA][CANCEL] error:", error)
            return False

    def get_order_status(self, order_id):
        try:
            order = self.kite.order_history(order_id)
            if order:
                return order[-1].get("status", "UNKNOWN")
        except Exception:
            pass
        return "UNKNOWN"