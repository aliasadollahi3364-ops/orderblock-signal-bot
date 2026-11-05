"""Internal-only demo trading ledger; no exchange order APIs are used."""
from __future__ import annotations

from dataclasses import dataclass, field
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
    positions: dict[str, DemoPosition] = field(default_factory=dict)

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

    @property
    def position(self) -> Optional[DemoPosition]:
        """Compatibility accessor for the most recently opened position."""
        return next(reversed(self.positions.values()), None) if self.positions else None

    @property
    def open_positions(self) -> list[DemoPosition]:
        return [p for p in self.positions.values() if not p.closed]

    @property
    def margin_used(self) -> float:
        return len(self.open_positions) * self.margin_per_trade

    @property
    def free_margin(self) -> float:
        assert self.balance is not None
        return max(0.0, self.balance - self.margin_used)

    def open_position(self, symbol: str, side: Side, entry: float, stop: float) -> DemoPosition:
        if symbol in self.positions and not self.positions[symbol].closed:
            raise ValueError(f"An open demo position already exists for {symbol}.")
        if entry <= 0 or stop <= 0:
            raise ValueError("Entry and stop must be positive.")
        if side is Side.LONG and stop >= entry:
            raise ValueError("Long stop must be below entry.")
        if side is Side.SHORT and stop <= entry:
            raise ValueError("Short stop must be above entry.")
        if self.free_margin < self.margin_per_trade:
            raise ValueError("Insufficient free demo margin.")

        qty = self.notional / entry
        risk = abs(entry - stop)
        fee = self.notional * self.fee_rate
        assert self.balance is not None
        self.balance -= fee
        targets = ((entry + risk, entry + 2 * risk, entry + 3 * risk)
                   if side is Side.LONG else
                   (entry - risk, entry - 2 * risk, entry - 3 * risk))
        position = DemoPosition(
            symbol=symbol, side=side, entry=entry, stop=stop, initial_stop=stop,
            quantity_initial=qty, quantity_remaining=qty,
            tp1=targets[0], tp2=targets[1], tp3=targets[2],
        )
        self.positions[symbol] = position
        return position

    def _close_fraction(self, pos: DemoPosition, fraction: float, price: float) -> float:
        qty = min(pos.quantity_initial * fraction, pos.quantity_remaining)
        if qty <= 0:
            return 0.0
        direction = 1.0 if pos.side is Side.LONG else -1.0
        gross = (price - pos.entry) * qty * direction
        fee = price * qty * self.fee_rate
        net = gross - fee
        pos.quantity_remaining = max(0.0, pos.quantity_remaining - qty)
        pos.realized_pnl += net
        assert self.balance is not None
        self.balance += gross - fee
        return net

    def process_candle(self, high: float, low: float, symbol: Optional[str] = None) -> list[dict[str, float | str]]:
        """Process one completed OHLC range; the stop wins ambiguous initial bars."""
        if high < low:
            raise ValueError("Candle high cannot be below low.")
        if symbol is None:
            active = self.open_positions
            if len(active) != 1:
                raise ValueError("Pass symbol when zero or multiple demo positions are open.")
            pos = active[0]
        else:
            pos = self.positions.get(symbol)
        if pos is None or pos.closed:
            return []

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
            moved_stop_hit = low <= pos.stop if long_side else high >= pos.stop
            if moved_stop_hit:
                self._close_fraction(pos, 1.0, pos.stop)
                pos.closed = True
                events.append({"type": "BREAK_EVEN_STOP", "price": pos.stop})
                return events

        if not pos.tp2_done and target_hit(pos.tp2):
            self._close_fraction(pos, 0.33, pos.tp2)
            pos.tp2_done = True
            pos.stop = pos.tp1
            events.append({"type": "TP2", "price": pos.tp2})
            moved_stop_hit = low <= pos.stop if long_side else high >= pos.stop
            if moved_stop_hit:
                self._close_fraction(pos, 1.0, pos.stop)
                pos.closed = True
                events.append({"type": "TP1_TRAILING_STOP", "price": pos.stop})
                return events

        if not pos.tp3_done and target_hit(pos.tp3):
            self._close_fraction(pos, 1.0, pos.tp3)
            pos.tp3_done = True
            pos.closed = True
            events.append({"type": "TP3", "price": pos.tp3})
        return events

    def unrealized_pnl(self, mark_price: float, symbol: Optional[str] = None) -> float:
        if symbol is not None:
            selected = [self.positions[symbol]] if symbol in self.positions else []
        else:
            selected = self.open_positions
            if len(selected) > 1:
                raise ValueError("Pass symbol when valuing multiple open positions.")
        total = 0.0
        for pos in selected:
            if pos.closed:
                continue
            direction = 1.0 if pos.side is Side.LONG else -1.0
            total += (mark_price - pos.entry) * pos.quantity_remaining * direction
        return total

    def equity(self, mark_prices: Optional[dict[str, float]] = None) -> float:
        assert self.balance is not None
        unrealized = 0.0
        if mark_prices:
            for symbol, mark in mark_prices.items():
                unrealized += self.unrealized_pnl(mark, symbol)
        return self.balance + unrealized
