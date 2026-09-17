import os
import time
from abc import ABC, abstractmethod


class ExchangeConnector(ABC):
    def __init__(self, trade_capital_inr=30000.0):
        self.trade_capital = float(os.getenv("TRADE_CAPITAL_INR", trade_capital_inr))

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def get_last_price(self, symbol):
        pass

    @abstractmethod
    def place_market_order(self, symbol, side, quantity):
        pass

    @abstractmethod
    def place_target_order(self, symbol, side, quantity, target_price):
        pass

    @abstractmethod
    def place_stoploss_order(self, symbol, side, quantity, sl_price):
        pass

    @abstractmethod
    def cancel_order(self, symbol, order_id):
        pass

    @abstractmethod
    def get_order_status(self, order_id):
        pass


class PaperExchange(ExchangeConnector):
    """
    Paper trading engine. Never touches real money.

    Prices are simulated by taking the last known price
    and moving it slightly each tick so TPs / SLs can be
    tested automatically.
    """

    def __init__(self, trade_capital_inr=30000.0):
        super().__init__(trade_capital_inr)
        self.cash = self.trade_capital
        self.positions = {}
        self.open_orders = {}
        self.sim_prices = {}
        self.tick = 0
        self.execution_delay_ms = 20

    def connect(self):
        print("[PAPER] Connected (no real money used)")
        return True

    def _simulate_price(self, symbol, base_price, direction="BUY"):
        self.tick += 1
        if symbol not in self.sim_prices:
            self.sim_prices[symbol] = {"buy": base_price, "sell": base_price, "open": False}

        state = self.sim_prices[symbol]
        state["open"] = True

        momentum = 1.0002 if direction == "BUY" else 0.9998
        noise = 1 + ((self.tick % 5) - 2) * 0.0001

        price = state["buy"] * momentum * noise

        state["buy"] = price
        state["sell"] = price * 1.0001

        return state["sell"]

    def get_last_price(self, symbol):
        return self.sim_prices.get(symbol, {}).get("sell")

    def _symbol_qty(self, symbol, price):
        if price <= 0:
            price = 1
        return round(self.trade_capital / price, 2)

    def place_market_order(self, symbol, side, quantity=None, ref_price=None):
        base_price = ref_price if ref_price else 100.0

        # Fresh entry -> rebase the simulated price on THIS signal's premium.
        # Without this, a second trade on the same symbol inherits where the
        # previous trade left off (e.g. 100 instead of 20), which made the
        # target look instantly "hit" and booked bogus TAKE_PROFIT losses.
        self.sim_prices[symbol] = {
            "buy": base_price,
            "sell": base_price,
            "open": False,
        }

        price = self._simulate_price(symbol, base_price, side)

        if quantity is None:
            quantity = self._symbol_qty(symbol, price)

        position_slot = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "entry_price": price,
            "targets": {},
            "stoploss": None,
            "status": "OPEN",
            "filled_at": time.time(),
        }

        self.positions[len(self.positions) + 1] = position_slot

        order_id = f"paper-{time.time_ns()}"

        self.open_orders[order_id] = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "type": "MARKET",
            "status": "FILLED",
            "price": price,
        }

        return order_id, position_slot

    def place_target_order(self, symbol, side, quantity, target_price):
        order_id = f"tp-{time.time_ns()}"

        self.open_orders[order_id] = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "type": "TARGET",
            "status": "OPEN",
            "price": target_price,
        }

        return order_id

    def place_stoploss_order(self, symbol, side, quantity, sl_price):
        order_id = f"sl-{time.time_ns()}"

        self.open_orders[order_id] = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "type": "STOPLOSS",
            "status": "OPEN",
            "price": sl_price,
        }

        return order_id

    def cancel_order(self, symbol, order_id):
        if order_id in self.open_orders:
            self.open_orders[order_id]["status"] = "CANCELLED"
            return True
        return False

    def get_order_status(self, order_id):
        return self.open_orders.get(order_id, {}).get("status", "UNKNOWN")