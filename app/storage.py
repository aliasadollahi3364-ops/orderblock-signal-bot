""""Atomic SQLite persistence for the internal demo account."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.demo import DemoAccount, DemoPosition, Side


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

    @staticmethod
    def _position_payload(position: DemoPosition) -> dict[str, Any]:
        data = asdict(position)
        data["side"] = position.side.value
        return data

    def save_state(self, account: DemoAccount, events: list[dict[str, Any]]) -> None:
        """Replace the account snapshot and event history atomically."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
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
                [(symbol, json.dumps(self._position_payload(pos), separators=(",", ":")))
                 for symbol, pos in account.positions.items()],
            )
            db.execute("DELETE FROM demo_events")
            db.executemany(
                "INSERT INTO demo_events(payload) VALUES (?)",
                [(json.dumps(event, separators=(",", ":"), default=str),) for event in events],
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
