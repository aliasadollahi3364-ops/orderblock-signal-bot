"""Two independent demo-only pending limit legs for confirmed setups.

This module never submits exchange orders. It only tracks simulated limit prices
and emits lifecycle events for the scanner/Telegram layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
        if any(price <= 0 for price in prices) or prices[0] == prices[1]:
            raise ValueError("two distinct positive limit prices are required")
        if self.side == "BUY":
            if not (self.stop < min(prices) < max(prices)):
                raise ValueError("BUY stop must be below both limit prices")
            if self.invalidation >= min(prices):
                raise ValueError("BUY invalidation must be below both limit prices")
        else:
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
                          volume_weighted_price: float) -> tuple[float, float]:
    """Return near and deep entries using the OB range and volume-weighted price.

    For BUY, near is above deep; for SELL, near is below deep. The VW price
    must lie inside the supplied OB. This is candle-volume weighting, not
    exchange order-book liquidity or a full volume-profile feed.
    """
    if side not in {"BUY", "SELL"} or ob_bottom <= 0 or ob_top <= ob_bottom:
        raise ValueError("invalid side or order-block range")
    if not ob_bottom <= volume_weighted_price <= ob_top:
        raise ValueError("volume-weighted price must be inside the OB")
    midpoint = (ob_top + ob_bottom) / 2.0
    if side == "BUY":
        deep = min(volume_weighted_price, midpoint)
        near = max(volume_weighted_price, midpoint)
    else:
        near = min(volume_weighted_price, midpoint)
        deep = max(volume_weighted_price, midpoint)
    if near == deep:
        # A flat VWAP/midpoint cannot create two separate orders; use a
        # small deterministic split of the OB range.
        quarter = (ob_top - ob_bottom) * 0.25
        if side == "BUY":
            near, deep = ob_bottom + 0.75 * (ob_top-ob_bottom), ob_bottom + 0.25 * (ob_top-ob_bottom)
        else:
            near, deep = ob_bottom + 0.25 * (ob_top-ob_bottom), ob_bottom + 0.75 * (ob_top-ob_bottom)
    return near, deep


class PendingLimitBook:
    """Tracks two independently fillable/cancellable limit legs per setup."""

    def __init__(self) -> None:
        self.setups: dict[str, PendingSetup] = {}

    def add(self, setup: PendingSetup) -> None:
        if setup.symbol in self.setups and self.setups[setup.symbol].active:
            raise ValueError(f"active pending setup already exists for {setup.symbol}")
        self.setups[setup.symbol] = setup

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
