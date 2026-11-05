"""Internal-only demo trading ledger.

This module never sends orders to an exchange. Prices and quantities use a
linear USDT-settled contract approximation; exchange-specific contract
metadata must be validated before production market-data integration.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class DemoPosition:
    symbol: str
    side: Side
    entry: float
    stop: float
    initial_stop: float
    quantity_initial: float
    quantity_remaining: float
    tp1: float
    tp2: float
    tp3: float
    tp1_done: bool = False
    tp2_done: bool = False
    tp3_done: bool = False
    closed: bool = False
    realized_pnl: float = 0.0

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.initial_stop)


@dataclass
class DemoAccount:
    initial_balance: float = 100.0
    margin_per_trade: float = 5.0
    leverage: float = 5.0
    fee_rate: float = 0.0005
    balance: Optional[float] = None
    position: Optional[DemoPosition] = None

    def __post_init__(self) -> None:
        if self.balance is None:
            self.balance = self.initial_balance
        if self.initial_balance <= 0 or self.margin_per_trade <= 0 or self.leverage <= 0:
            raise ValueError("Demo balance, margin and leverage must be positive.")
        if self.fee_rate < 0:
            raise ValueError("Fee rate cannot be negative.")

    @property
    def notional(self) -> float:
        return self.margin_per_trade * self.leverage

    def open_position(self, symbol: str, side: Side, entry: float, stop: float) -> DemoPosition:
        if self.position is not None and not self.position.closed:
            raise ValueError("Only one demo position is supported per account.")
        if entry <= 0 or stop <= 0:
            raise ValueError("Entry and stop must be positive.")
        if side is Side.LONG and stop >= entry:
            raise ValueError("Long stop must be below entry.")
        if side is Side.SHORT and stop <= entry:
            raise ValueError("Short stop must be above entry.")
        if self.balance is None or self.balance < self.margin_per_trade:
            raise ValueError("Insufficient demo balance for required margin.")

        qty = self.notional / entry
        risk = abs(entry - stop)
        fee = self.notional * self.fee_rate
        self.balance -= fee
        if side is Side.LONG:
            targets = (entry + risk, entry + 2 * risk, entry + 3 * risk)
        else:
            targets = (entry - risk, entry - 2 * risk, entry - 3 * risk)

        self.position = DemoPosition(
            symbol=symbol,
            side=side,
            entry=entry,
            stop=stop,
            initial_stop=stop,
            quantity_initial=qty,
            quantity_remaining=qty,
            tp1=targets[0],
            tp2=targets[1],
            tp3=targets[2],
        )
        return self.position

    def _close_fraction(self, pos: DemoPosition, fraction: float, price: float) -> float:
        qty = min(pos.quantity_initial * fraction, pos.quantity_remaining)
        if qty <= 0:
            return 0.0
        direction = 1.0 if pos.side is Side.LONG else -1.0
        gross = (price - pos.entry) * qty * direction
        fee = price * qty * self.fee_rate
        net = gross - fee
        pos.quantity_remaining -= qty
        pos.realized_pnl += net
        assert self.balance is not None
        self.balance += gross - fee
        return net

    def process_candle(self, high: float, low: float) -> list[dict[str, float | str]]:
        """Process one completed candle; stop takes priority on ambiguous bars."""
        pos = self.position
        if pos is None or pos.closed:
            return []
        if high < low:
            raise ValueError("Candle high cannot be below low.")

        events: list[dict[str, float | str]] = []
        long_side = pos.side is Side.LONG
        stop_hit = low <= pos.stop if long_side else high >= pos.stop
        if stop_hit:
            exit_price = pos.stop
            self._close_fraction(pos, 1.0, exit_price)
            pos.closed = True
            events.append({"type": "STOP", "price": exit_price})
            return events

        def target_hit(target: float) -> bool:
            return high >= target if long_side else low <= target

        if not pos.tp1_done and target_hit(pos.tp1):
            self._close_fraction(pos, 0.33, pos.tp1)
            pos.tp1_done = True
            pos.stop = pos.entry
            events.append({"type": "TP1", "price": pos.tp1})
        if not pos.tp2_done and target_hit(pos.tp2):
            self._close_fraction(pos, 0.33, pos.tp2)
            pos.tp2_done = True
            pos.stop = pos.tp1
            events.append({"type": "TP2", "price": pos.tp2})
        if not pos.tp3_done and target_hit(pos.tp3):
            self._close_fraction(pos, 1.0, pos.tp3)
            pos.tp3_done = True
            pos.closed = True
            events.append({"type": "TP3", "price": pos.tp3})
        return events

    def unrealized_pnl(self, mark_price: float) -> float:
        pos = self.position
        if pos is None or pos.closed:
            return 0.0
        direction = 1.0 if pos.side is Side.LONG else -1.0
        return (mark_price - pos.entry) * pos.quantity_remaining * direction

    def equity(self, mark_price: Optional[float] = None) -> float:
        assert self.balance is not None
        return self.balance + (self.unrealized_pnl(mark_price) if mark_price is not None else 0.0)
