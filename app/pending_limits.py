"""Two independent demo-only pending limit legs for confirmed setups.

This module never submits exchange orders. It only tracks simulated limit prices
and emits lifecycle events for the scanner/Telegram layer.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from enum import Enum


class PendingStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


@dataclass
class PendingLimit:
    number: int
    price: float
    status: PendingStatus = PendingStatus.PENDING
    filled_at: int | None = None
    cancelled_at: int | None = None
    cancel_reason: str | None = None


@dataclass
class PendingSetup:
    symbol: str
    side: str
    created_at: int
    expires_after_bars: int
    invalidation: float
    stop: float
    ob_top: float
    ob_bottom: float
    limits: list[PendingLimit]
    last_processed_bar: int | None = None

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if len(self.limits) != 2:
            raise ValueError("exactly two independent limit legs are required")
        if self.expires_after_bars < 1:
            raise ValueError("expires_after_bars must be positive")
        prices = [leg.price for leg in self.limits]
        numeric_values = [self.invalidation, self.stop, self.ob_top, self.ob_bottom, *prices]
        if not all(math.isfinite(value) for value in numeric_values):
            raise ValueError("setup prices must be finite")
        if self.ob_bottom <= 0 or self.ob_top <= self.ob_bottom:
            raise ValueError("invalid order-block range")
        if [leg.number for leg in self.limits] != [1, 2]:
            raise ValueError("limit legs must be numbered exactly 1 and 2")
        if any(price <= 0 for price in prices) or prices[0] == prices[1]:
            raise ValueError("two distinct positive limit prices are required")
        if any(not self.ob_bottom <= price <= self.ob_top for price in prices):
            raise ValueError("both limit prices must remain inside the order block")
        if self.side == "BUY":
            if not prices[1] < prices[0]:
                raise ValueError("BUY limit 1 must be nearer/higher than limit 2")
            if not (self.stop < min(prices) < max(prices)):
                raise ValueError("BUY stop must be below both limit prices")
            if self.invalidation >= min(prices):
                raise ValueError("BUY invalidation must be below both limit prices")
        else:
            if not prices[0] < prices[1]:
                raise ValueError("SELL limit 1 must be nearer/lower than limit 2")
            if not (self.stop > max(prices) > min(prices)):
                raise ValueError("SELL stop must be above both limit prices")
            if self.invalidation <= max(prices):
                raise ValueError("SELL invalidation must be above both limit prices")

    @property
    def active(self) -> bool:
        return any(leg.status is PendingStatus.PENDING for leg in self.limits)


@dataclass(frozen=True)
class PendingEvent:
    symbol: str
    side: str
    leg_number: int
    status: str
    price: float
    timestamp: int
    reason: str = ""


def make_two_limit_prices(side: str, ob_top: float, ob_bottom: float,
                          volume_weighted_price: float,
                          reference_price: float | None = None) -> tuple[float, float]:
    """Return near/deep pullback entries inside the OB and on the correct side
    of the signal price.

    BUY entries are strictly below the signal price, with leg 1 nearer/higher
    and leg 2 deeper/lower. SELL entries are strictly above the signal price,
    with leg 1 nearer/lower and leg 2 deeper/higher. The VW price is used to
    position the two entries within the eligible part of the zone.
    """
    values = (ob_top, ob_bottom, volume_weighted_price)
    if (side not in {"BUY", "SELL"} or
            not all(math.isfinite(value) for value in values) or
            ob_bottom <= 0 or ob_top <= ob_bottom):
        raise ValueError("invalid side or order-block range")
    if not ob_bottom <= volume_weighted_price <= ob_top:
        raise ValueError("volume-weighted price must be inside the OB")

    lower, upper = ob_bottom, ob_top
    if reference_price is not None:
        if not math.isfinite(reference_price) or reference_price <= 0:
            raise ValueError("reference price must be positive and finite")
        # Keep a tiny strict gap so an entry is never placed at or beyond the
        # breakout candle's close. The available interval is still clipped to
        # the actual order block.
        epsilon = max(abs(reference_price) * 1e-9, (ob_top - ob_bottom) * 1e-9, 1e-12)
        if side == "BUY":
            upper = min(upper, reference_price - epsilon)
        else:
            lower = max(lower, reference_price + epsilon)
        if upper - lower <= epsilon:
            raise ValueError("order block has no room for two pullback limits beyond the signal price")

    midpoint = (upper + lower) / 2.0
    weighted = min(upper, max(lower, volume_weighted_price))
    if side == "BUY":
        near, deep = max(weighted, midpoint), min(weighted, midpoint)
    else:
        near, deep = min(weighted, midpoint), max(weighted, midpoint)

    if near - deep <= max(abs(midpoint) * 1e-12, 1e-12):
        # A flat VW price at the midpoint cannot split the two legs; use
        # deterministic quarter points in the eligible OB interval.
        span = upper - lower
        if side == "BUY":
            near, deep = lower + 0.75 * span, lower + 0.25 * span
        else:
            near, deep = lower + 0.25 * span, lower + 0.75 * span

    if side == "BUY" and not (lower <= deep < near <= upper):
        raise ValueError("BUY pullback limits are not correctly ordered")
    if side == "SELL" and not (lower <= near < deep <= upper):
        raise ValueError("SELL pullback limits are not correctly ordered")
    return near, deep


class PendingLimitBook:
    """Tracks two independently fillable/cancellable limit legs per setup."""

    def __init__(self) -> None:
        self.setups: dict[str, PendingSetup] = {}

    def add(self, setup: PendingSetup) -> None:
        if setup.symbol in self.setups and self.setups[setup.symbol].active:
            raise ValueError(f"active pending setup already exists for {setup.symbol}")
        self.setups[setup.symbol] = setup

    def can_start_setup(self, symbol: str, *, position_open: bool = False,
                        created_at: int | None = None) -> bool:
        """Return whether a fresh setup may be created without duplicating exposure.

        An open demo position or an active pending setup blocks another entry.
        The same signal timestamp is also blocked after a setup completes, which
        prevents replayed candles from recreating an already-consumed setup.
        """
        if position_open:
            return False
        existing = self.setups.get(symbol)
        if existing is None:
            return True
        if existing.active:
            return False
        if created_at is not None and existing.created_at == created_at:
            return False
        return True

    def process_candle(self, symbol: str, timestamp: int, high: float,
                       low: float, close: float) -> list[PendingEvent]:
        setup = self.setups.get(symbol)
        if setup is None:
            return []
        if high < low or low <= 0:
            raise ValueError("invalid candle range")
        if setup.last_processed_bar is not None and timestamp <= setup.last_processed_bar:
            return []
        # Keep a durable cursor even after both legs are filled/cancelled:
        # an open demo position may still need each later candle after restart.
        setup.last_processed_bar = timestamp
        # created_at and processed timestamps are candle close-times. The
        # confirmation candle itself must never fill its own pullback orders.
        if timestamp <= setup.created_at or not setup.active:
            return []
        events: list[PendingEvent] = []

        # A wick through the structural stop invalidates the setup too. If a
        # candle touches both a limit and invalidation, cancellation wins.
        invalidated = (low <= setup.invalidation if setup.side == "BUY"
                       else high >= setup.invalidation)
        expired = timestamp >= setup.created_at + setup.expires_after_bars * 15 * 60 * 1000
        if invalidated or expired:
            reason = "ساختار بازار بی‌اعتبار شد" if invalidated else "مهلت انتظار سفارش تمام شد"
            for leg in setup.limits:
                if leg.status is PendingStatus.PENDING:
                    leg.status = PendingStatus.CANCELLED
                    leg.cancelled_at = timestamp
                    leg.cancel_reason = reason
                    events.append(PendingEvent(symbol, setup.side, leg.number,
                                                "CANCELLED", leg.price, timestamp, reason))
            return events

        for leg in setup.limits:
            if leg.status is not PendingStatus.PENDING:
                continue
            touched = low <= leg.price <= high
            if touched:
                leg.status = PendingStatus.FILLED
                leg.filled_at = timestamp
                events.append(PendingEvent(symbol, setup.side, leg.number,
                                            "FILLED", leg.price, timestamp))
        return events

    def cancel(self, symbol: str, timestamp: int, reason: str) -> list[PendingEvent]:
        setup = self.setups.get(symbol)
        if setup is None:
            return []
        events = []
        for leg in setup.limits:
            if leg.status is PendingStatus.PENDING:
                leg.status = PendingStatus.CANCELLED
                leg.cancelled_at = timestamp
                leg.cancel_reason = reason
                events.append(PendingEvent(symbol, setup.side, leg.number,
                                            "CANCELLED", leg.price, timestamp, reason))
        return events
