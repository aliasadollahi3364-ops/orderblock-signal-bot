"""Internal-only demo trading ledger; no exchange order APIs are used."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
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
    opened_at: str | None = None
    # None means a legacy position that reserved the full configured margin.
    margin_allocated: float | None = None
    legs: list[dict] = field(default_factory=list)

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
        return sum(self.margin_per_trade if p.margin_allocated is None else p.margin_allocated for p in self.open_positions)

    @property
    def free_margin(self) -> float:
        assert self.balance is not None
        return max(0.0, self.balance - self.margin_used)

    def open_position(self, symbol: str, side: Side, entry: float, stop: float, opened_at: str | None = None) -> DemoPosition:
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
            opened_at=opened_at or datetime.now(timezone.utc).isoformat(),
            margin_allocated=self.margin_per_trade,
        )
        self.positions[symbol] = position
        return position

    def add_limit_fill(self, symbol: str, side: Side, entry: float, stop: float, opened_at: str | None = None, leg_number: int = 1) -> DemoPosition:
        """Add one half-size simulated limit fill to an aggregated position."""
        if entry <= 0 or stop <= 0:
            raise ValueError("Entry and stop must be positive.")
        if side is Side.LONG and stop >= entry:
            raise ValueError("Long stop must be below entry.")
        if side is Side.SHORT and stop <= entry:
            raise ValueError("Short stop must be above entry.")
        margin = self.margin_per_trade / 2.0
        position = self.positions.get(symbol)
        if position is not None and not position.closed:
            allocated = self.margin_per_trade if position.margin_allocated is None else position.margin_allocated
            if position.side is not side or abs(position.initial_stop - stop) > max(entry, stop) * 1e-10:
                raise ValueError("A pending fill conflicts with the existing demo position.")
            if position.tp1_done or position.tp2_done or position.tp3_done:
                raise ValueError("Cannot add a limit fill after a profit target has triggered.")
            if allocated + margin > self.margin_per_trade + 1e-9:
                raise ValueError("The demo position has already used its allocated margin.")
            if self.free_margin < margin:
                raise ValueError("Insufficient free demo margin.")
            new_qty = margin * self.leverage / entry
            old_qty = position.quantity_initial
            total_qty = old_qty + new_qty
            position.entry = (position.entry * old_qty + entry * new_qty) / total_qty
            position.quantity_initial = total_qty
            position.quantity_remaining += new_qty
            position.margin_allocated = allocated + margin
            risk = abs(entry - stop)
            targets_for_leg = ((entry + risk, entry + 2 * risk, entry + 3 * risk)
                               if side is Side.LONG else
                               (entry - risk, entry - 2 * risk, entry - 3 * risk))
            position.legs.append({
                "number": leg_number, "entry": entry, "stop": stop,
                "quantity_initial": new_qty, "quantity_remaining": new_qty,
                "tp1": targets_for_leg[0], "tp2": targets_for_leg[1], "tp3": targets_for_leg[2],
                "tp1_done": False, "tp2_done": False, "tp3_done": False,
            })
            targets = ((position.entry + risk, position.entry + 2 * risk, position.entry + 3 * risk) if side is Side.LONG else (position.entry - risk, position.entry - 2 * risk, position.entry - 3 * risk))
            position.tp1, position.tp2, position.tp3 = targets
            assert self.balance is not None
            self.balance -= margin * self.leverage * self.fee_rate
            return position
        if self.free_margin < margin:
            raise ValueError("Insufficient free demo margin.")
        qty = margin * self.leverage / entry
        risk = abs(entry - stop)
        targets = ((entry + risk, entry + 2 * risk, entry + 3 * risk) if side is Side.LONG else (entry - risk, entry - 2 * risk, entry - 3 * risk))
        assert self.balance is not None
        self.balance -= margin * self.leverage * self.fee_rate
        position = DemoPosition(
            symbol=symbol, side=side, entry=entry, stop=stop, initial_stop=stop,
            quantity_initial=qty, quantity_remaining=qty,
            tp1=targets[0], tp2=targets[1], tp3=targets[2],
            opened_at=opened_at or datetime.now(timezone.utc).isoformat(),
            margin_allocated=margin,
            legs=[{
                "number": leg_number, "entry": entry, "stop": stop,
                "quantity_initial": qty, "quantity_remaining": qty,
                "tp1": targets[0], "tp2": targets[1], "tp3": targets[2],
                "tp1_done": False, "tp2_done": False, "tp3_done": False,
            }],
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

    def _close_leg_fraction(self, pos: DemoPosition, leg: dict, fraction: float, price: float) -> None:
        qty = min(leg["quantity_initial"] * fraction, leg["quantity_remaining"])
        if qty <= 0:
            return
        direction = 1.0 if pos.side is Side.LONG else -1.0
        gross = (price - leg["entry"]) * qty * direction
        fee = price * qty * self.fee_rate
        leg["quantity_remaining"] = max(0.0, leg["quantity_remaining"] - qty)
        pos.realized_pnl += gross - fee
        assert self.balance is not None
        self.balance += gross - fee

    def _process_limit_legs(self, pos: DemoPosition, high: float, low: float) -> list[dict[str, float | str]]:
        events: list[dict[str, float | str]] = []
        long_side = pos.side is Side.LONG
        for leg in pos.legs:
            if leg["quantity_remaining"] <= 1e-12:
                continue
            stop_hit = low <= leg["stop"] if long_side else high >= leg["stop"]
            if stop_hit:
                price = leg["stop"]
                self._close_leg_fraction(pos, leg, 1.0, price)
                events.append({"type": "STOP", "price": price, "leg": leg["number"]})
                continue

            def target_hit(target: float) -> bool:
                return high >= target if long_side else low <= target

            if not leg["tp1_done"] and target_hit(leg["tp1"]):
                self._close_leg_fraction(pos, leg, 0.33, leg["tp1"])
                leg["tp1_done"] = True
                leg["stop"] = leg["entry"]
                events.append({"type": "TP1", "price": leg["tp1"], "leg": leg["number"]})
                moved_stop_hit = low <= leg["stop"] if long_side else high >= leg["stop"]
                if moved_stop_hit:
                    self._close_leg_fraction(pos, leg, 1.0, leg["stop"])
                    events.append({"type": "BREAK_EVEN_STOP", "price": leg["stop"], "leg": leg["number"]})
                    continue

            if not leg["tp2_done"] and target_hit(leg["tp2"]):
                self._close_leg_fraction(pos, leg, 0.33, leg["tp2"])
                leg["tp2_done"] = True
                leg["stop"] = leg["tp1"]
                events.append({"type": "TP2", "price": leg["tp2"], "leg": leg["number"]})
                moved_stop_hit = low <= leg["stop"] if long_side else high >= leg["stop"]
                if moved_stop_hit:
                    self._close_leg_fraction(pos, leg, 1.0, leg["stop"])
                    events.append({"type": "TP1_TRAILING_STOP", "price": leg["stop"], "leg": leg["number"]})
                    continue

            if not leg["tp3_done"] and target_hit(leg["tp3"]):
                self._close_leg_fraction(pos, leg, 1.0, leg["tp3"])
                leg["tp3_done"] = True
                events.append({"type": "TP3", "price": leg["tp3"], "leg": leg["number"]})

        pos.quantity_remaining = sum(leg["quantity_remaining"] for leg in pos.legs)
        pos.tp1_done = all(leg["tp1_done"] or leg["quantity_remaining"] <= 1e-12 for leg in pos.legs)
        pos.tp2_done = all(leg["tp2_done"] or leg["quantity_remaining"] <= 1e-12 for leg in pos.legs)
        pos.tp3_done = all(leg["tp3_done"] or leg["quantity_remaining"] <= 1e-12 for leg in pos.legs)
        pos.closed = pos.quantity_remaining <= 1e-12
        return events

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
        if pos.legs:
            return self._process_limit_legs(pos, high, low)

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
