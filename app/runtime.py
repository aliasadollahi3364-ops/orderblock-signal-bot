"""Shared runtime state for API, Telegram, scanner and persistence."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.demo import DemoAccount
from app.strategy import StrategyConfig
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

    def persist_demo_state(self) -> None:
        if self.storage is None:
            return
        save_snapshot = getattr(self.storage, "save_runtime_state", None)
        if save_snapshot is not None:
            save_snapshot(self.account, self.closed_trade_events, self.pending_limits)
        else:
            self.storage.save_state(self.account, self.closed_trade_events)

    def persist_pending_limits(self) -> None:
        if self.storage is None:
            return
        # A FILLED marker must never reach disk before its matching demo ledger
        # leg exists. Otherwise a crash between the two writes loses the entry.
        for symbol, setup in self.pending_limits.setups.items():
            filled_numbers = {
                leg.number for leg in setup.limits
                if leg.status is PendingStatus.FILLED
            }
            if not filled_numbers:
                continue
            position = self.account.positions.get(symbol)
            persisted_numbers = {
                int(leg.get("number", -1)) for leg in (position.legs if position else [])
            }
            if not filled_numbers.issubset(persisted_numbers):
                # The following persist_demo_state() call commits both snapshots.
                return
        save_snapshot = getattr(self.storage, "save_runtime_state", None)
        if save_snapshot is not None:
            save_snapshot(self.account, self.closed_trade_events, self.pending_limits)
        else:
            self.storage.save_pending_setups(self.pending_limits)


runtime = BotRuntime()
