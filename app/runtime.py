"""Shared runtime state for API, Telegram, scanner and persistence."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.demo import DemoAccount


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

    def persist_demo_state(self) -> None:
        if self.storage is not None:
            self.storage.save_state(self.account, self.closed_trade_events)


runtime = BotRuntime()
