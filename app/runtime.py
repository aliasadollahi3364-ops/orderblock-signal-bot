"""Shared runtime state for API, Telegram, scanner and persistence."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.demo import DemoAccount
from app.strategy_pine_v6 import StrategyConfig
from app.pending_limits import PendingLimitBook, PendingStatus


@dataclass
class BotRuntime:
    account: DemoAccount = field(default_factory=DemoAccount)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    scanner_state: str = "NOT_STARTED"
    last_scan_at: datetime | None = None
    selected_symbols: list[str] = field(default_factory=list)
    manual_symbols: list[str] = field(default_factory=list)
    signal_count: int = 0
    closed_trade_events: list[dict[str, Any]] = field(default_factory=list)
    storage: Any = None
    market_symbol_resolver: Any = None
    strategy_config: StrategyConfig = field(default_factory=StrategyConfig)
    gold_market: Any = None
    pending_limits: PendingLimitBook = field(default_factory=PendingLimitBook)

    def _pending_fills_reconciled(self) -> bool:
        # A candle can fill both limits at once. The book marks both FILLED
        # before the scanner books each demo leg, so never persist a partial
        # account snapshot against a book that already records both fills.
        for symbol, setup in self.pending_limits.setups.items():
            position = self.account.positions.get(symbol)
            persisted_legs = position.legs if position else []
            for pending_leg in setup.limits:
                if pending_leg.status is not PendingStatus.FILLED:
                    continue
                matching = any(
                    int(leg.get("number", -1)) == pending_leg.number
                    and abs(float(leg.get("entry", 0.0)) - pending_leg.price)
                    <= max(abs(pending_leg.price), 1.0) * 1e-10
                    for leg in persisted_legs
                )
                if not matching:
                    return False
        return True

    def persist_demo_state(self) -> None:
        if self.storage is None or not self._pending_fills_reconciled():
            return
        save_snapshot = getattr(self.storage, "save_runtime_state", None)
        if save_snapshot is not None:
            save_snapshot(self.account, self.closed_trade_events, self.pending_limits)
        else:
            self.storage.save_state(self.account, self.closed_trade_events)

    def persist_pending_limits(self) -> None:
        if self.storage is None or not self._pending_fills_reconciled():
            # Do not commit a FILLED marker until every corresponding demo leg
            # has been booked. The next successful snapshot writes both tables.
            return
        save_snapshot = getattr(self.storage, "save_runtime_state", None)
        if save_snapshot is not None:
            save_snapshot(self.account, self.closed_trade_events, self.pending_limits)
        else:
            self.storage.save_pending_setups(self.pending_limits)


runtime = BotRuntime()
