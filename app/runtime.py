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
        # leg exists. Match number and entry price to avoid confusing a new setup
        # with an older, already-closed position for the same symbol.
        for symbol, setup in self.pending_limits.setups.items():
            filled_legs = [leg for leg in setup.limits if leg.status is PendingStatus.FILLED]
            if not filled_legs:
                continue
            position = self.account.positions.get(symbol)
            persisted_legs = position.legs if position else []
            for pending_leg in filled_legs:
                matching = any(
                    int(leg.get("number", -1)) == pending_leg.number
                    and abs(float(leg.get("entry", 0.0)) - pending_leg.price)
                    <= max(abs(pending_leg.price), 1.0) * 1e-10
                    for leg in persisted_legs
                )
                if not matching:
                    # The following persist_demo_state() call commits both snapshots.
                    return
        save_snapshot = getattr(self.storage, "save_runtime_state", None)
        if save_snapshot is not None:
            save_snapshot(self.account, self.closed_trade_events, self.pending_limits)
        else:
            self.storage.save_pending_setups(self.pending_limits)


runtime = BotRuntime()
