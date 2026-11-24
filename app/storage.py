"""Atomic SQLite persistence for the internal demo account."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.demo import DemoAccount, DemoPosition, Side
from app.strategy import StrategyConfig
from app.pending_limits import PendingLimit, PendingLimitBook, PendingSetup, PendingStatus


class SQLiteStore:
    """Persist the complete demo ledger in one SQLite transaction per snapshot."""

    def __init__(self, database_path: str):
        self.database_path = database_path
        if database_path != ":memory:":
            Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS demo_account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    initial_balance REAL NOT NULL,
                    margin_per_trade REAL NOT NULL,
                    leverage REAL NOT NULL,
                    fee_rate REAL NOT NULL,
                    balance REAL NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS demo_positions (
                    symbol TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS scanner_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS demo_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS pending_setups (
                    symbol TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    event_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_notification_outbox_pending
                ON notification_outbox(delivered_at, created_at)
            """)

    @staticmethod
    def _position_payload(position: DemoPosition) -> dict[str, Any]:
        data = asdict(position)
        data["side"] = position.side.value
        return data

    @staticmethod
    def _pending_rows(book: PendingLimitBook) -> list[tuple[str, str]]:
        rows = []
        for symbol, setup in book.setups.items():
            payload = {
                "symbol": setup.symbol, "side": setup.side,
                "created_at": setup.created_at,
                "expires_after_bars": setup.expires_after_bars,
                "invalidation": setup.invalidation, "stop": setup.stop,
                "ob_top": setup.ob_top, "ob_bottom": setup.ob_bottom,
                "last_processed_bar": setup.last_processed_bar,
                "limits": [{
                    "number": leg.number, "price": leg.price,
                    "status": leg.status.value, "filled_at": leg.filled_at,
                    "cancelled_at": leg.cancelled_at,
                    "cancel_reason": leg.cancel_reason,
                } for leg in setup.limits],
            }
            rows.append((symbol, json.dumps(payload, separators=(",", ":"))))
        return rows

    @staticmethod
    def _write_account(db, account: DemoAccount, events: list[dict[str, Any]]) -> None:
        db.execute("""
            INSERT INTO demo_account
                (id, initial_balance, margin_per_trade, leverage, fee_rate, balance)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                initial_balance=excluded.initial_balance,
                margin_per_trade=excluded.margin_per_trade,
                leverage=excluded.leverage,
                fee_rate=excluded.fee_rate,
                balance=excluded.balance
        """, (
            account.initial_balance, account.margin_per_trade, account.leverage,
            account.fee_rate, float(account.balance or 0.0),
        ))
        db.execute("DELETE FROM demo_positions")
        db.executemany(
            "INSERT INTO demo_positions(symbol, payload) VALUES (?, ?)",
            [(symbol, json.dumps(SQLiteStore._position_payload(pos), separators=(",", ":")))
             for symbol, pos in account.positions.items()],
        )
        db.execute("DELETE FROM demo_events")
        db.executemany(
            "INSERT INTO demo_events(payload) VALUES (?)",
            [(json.dumps(event, separators=(",", ":"), default=str),) for event in events],
        )

    def save_state(self, account: DemoAccount, events: list[dict[str, Any]]) -> None:
        """Replace the account snapshot and event history atomically."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._write_account(db, account, events)

    def save_runtime_state(self, account: DemoAccount, events: list[dict[str, Any]],
                           book: PendingLimitBook) -> None:
        """Atomically persist fills, positions, replay cursor and pending-leg states."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")

            # Enqueue trade lifecycle events in the same SQLite transaction
            # as the account snapshot. Hashes make repeated snapshots idempotent.
            old_event_signatures = {
                hashlib.sha256(json.dumps(
                    json.loads(row["payload"]), sort_keys=True,
                    separators=(",", ":"), default=str,
                ).encode("utf-8")).hexdigest()
                for row in db.execute("SELECT payload FROM demo_events")
            }
            notify_types = {
                "LIMIT_FILLED", "TP", "TP1", "TP2", "TP3", "STOP",
                "BREAK_EVEN_STOP", "TP1_TRAILING_STOP",
            }
            for event in events:
                if event.get("type") not in notify_types:
                    continue
                signature = hashlib.sha256(json.dumps(
                    event, sort_keys=True, separators=(",", ":"), default=str,
                ).encode("utf-8")).hexdigest()
                if signature in old_event_signatures:
                    continue  # Do not replay pre-outbox history after migration.
                payload = json.dumps(
                    {"kind": "trade", "event": event},
                    sort_keys=True, separators=(",", ":"), default=str,
                )
                event_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                db.execute(
                    "INSERT OR IGNORE INTO notification_outbox"
                    "(event_id, payload, created_at) VALUES (?, ?, ?)",
                    (event_id, payload, str(event.get("time", ""))),
                )

            old_pending = {}
            for row in db.execute("SELECT symbol, payload FROM pending_setups"):
                old_data = json.loads(row["payload"])
                for leg in old_data.get("limits", []):
                    old_pending[(row["symbol"], int(leg["number"]))] = leg.get("status")

            new_pending_rows = self._pending_rows(book)
            for symbol, raw_payload in new_pending_rows:
                setup_data = json.loads(raw_payload)
                for leg in setup_data.get("limits", []):
                    key = (symbol, int(leg["number"]))
                    if leg.get("status") != PendingStatus.CANCELLED.value:
                        continue
                    if old_pending.get(key) == PendingStatus.CANCELLED.value:
                        continue
                    event = {
                        "symbol": symbol, "side": setup_data.get("side"),
                        "type": "LIMIT_CANCELLED", "leg": leg["number"],
                        "price": leg["price"],
                        "time": leg.get("cancelled_at") or setup_data.get("created_at"),
                        "reason": leg.get("cancel_reason") or "نامشخص",
                    }
                    payload = json.dumps(
                        {"kind": "pending", "event": event},
                        sort_keys=True, separators=(",", ":"), default=str,
                    )
                    event_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                    db.execute(
                        "INSERT OR IGNORE INTO notification_outbox"
                        "(event_id, payload, created_at) VALUES (?, ?, ?)",
                        (event_id, payload, str(event.get("time", ""))),
                    )

            self._write_account(db, account, events)
            db.execute("DELETE FROM pending_setups")
            db.executemany(
                "INSERT INTO pending_setups(symbol, payload) VALUES (?, ?)",
                new_pending_rows,
            )

    def load_notification_outbox(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT event_id, payload FROM notification_outbox "
                "WHERE delivered_at IS NULL ORDER BY created_at, event_id LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
            return [
                {"event_id": row["event_id"], **json.loads(row["payload"])}
                for row in rows
            ]

    def mark_notification_delivered(self, event_id: str, delivered_at: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE notification_outbox SET delivered_at=? "
                "WHERE event_id=? AND delivered_at IS NULL",
                (delivered_at, event_id),
            )

    def save_manual_symbols(self, symbols: list[str]) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO scanner_settings(key, value) VALUES ('manual_symbols', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(sorted(set(symbols))),),
            )

    def load_manual_symbols(self) -> list[str]:
        with self._connect() as db:
            row = db.execute("SELECT value FROM scanner_settings WHERE key='manual_symbols'").fetchone()
            return json.loads(row["value"]) if row else []

    def save_strategy_config(self, config: StrategyConfig) -> None:
        values = asdict(config)
        values["require_trendline_pullback"] = True
        values["use_trailing_stop"] = False
        with self._connect() as db:
            db.execute(
                "INSERT INTO scanner_settings(key, value) VALUES ('strategy_config', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(values, separators=(",", ":")),),
            )

    def load_strategy_config(self) -> StrategyConfig:
        with self._connect() as db:
            row = db.execute("SELECT value FROM scanner_settings WHERE key='strategy_config'").fetchone()
            if row is None:
                return StrategyConfig()
            try:
                values = json.loads(row["value"])
                if "ob_atr_len" not in values or "enable_trendline_signals" not in values:
                    return StrategyConfig()
                allowed = set(StrategyConfig.__dataclass_fields__)
                values = {key: value for key, value in values.items() if key in allowed}
                values["require_trendline_pullback"] = True
                values["use_trailing_stop"] = False
                return StrategyConfig(**values)
            except (TypeError, ValueError, json.JSONDecodeError):
                return StrategyConfig()

    def load_state(self, default_account: DemoAccount) -> tuple[DemoAccount, list[dict[str, Any]]]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM demo_account WHERE id=1").fetchone()
            if row is None:
                return default_account, []
            account = DemoAccount(
                initial_balance=row["initial_balance"],
                margin_per_trade=row["margin_per_trade"],
                leverage=row["leverage"],
                fee_rate=row["fee_rate"],
                balance=row["balance"],
            )
            for item in db.execute("SELECT payload FROM demo_positions ORDER BY rowid"):
                data = json.loads(item["payload"])
                data["side"] = Side(data["side"])
                position = DemoPosition(**data)
                account.positions[position.symbol] = position
            events = [
                json.loads(item["payload"])
                for item in db.execute("SELECT payload FROM demo_events ORDER BY id")
            ]
            return account, events

    def save_pending_setups(self, book: PendingLimitBook) -> None:
        """Persist pending setups atomically, including completed/cancelled leg states."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM pending_setups")
            db.executemany(
                "INSERT INTO pending_setups(symbol, payload) VALUES (?, ?)",
                self._pending_rows(book),
            )

    def load_pending_setups(self) -> PendingLimitBook:
        """Restore pending setups after a process restart."""
        book = PendingLimitBook()
        with self._connect() as db:
            for row in db.execute("SELECT payload FROM pending_setups ORDER BY rowid"):
                data = json.loads(row["payload"])
                legs = [PendingLimit(
                    number=item["number"], price=item["price"],
                    status=PendingStatus(item["status"]),
                    filled_at=item.get("filled_at"),
                    cancelled_at=item.get("cancelled_at"),
                    cancel_reason=item.get("cancel_reason"),
                ) for item in data.pop("limits")]
                setup = PendingSetup(limits=legs, **data)
                book.setups[setup.symbol] = setup
        return book
