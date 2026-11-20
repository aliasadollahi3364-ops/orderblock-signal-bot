"""Talasanj adapter and persistent local candles for Iranian 18k gold.

Prices are indicative Iranian-market data in toman. This module never places orders.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.strategy import Candle, OrderBlockStrategy, Signal, StrategyConfig

logger = logging.getLogger(__name__)

BASE_URL = "https://talasanj.info/api/v1"
POLL_SECONDS = 20
TIMEFRAMES_MS = {"5m": 5 * 60_000, "15m": 15 * 60_000, "1h": 60 * 60_000, "4h": 4 * 60 * 60_000}


def _timestamp_ms(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value if value > 10_000_000_000 else value * 1000)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


class TalasanjClient:
    def __init__(self, api_key: str, client_name: str = "OrderBlock Signal Bot",
                 client_url: str = "https://github.com/aliasadollahi3364-ops/orderblock-signal-bot"):
        if not api_key.strip():
            raise ValueError("TALASANJ_API_KEY is required.")
        self.api_key = api_key.strip()
        self.client_name = client_name
        self.client_url = client_url

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, headers={
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "X-Client-Name": self.client_name,
            "X-Client-Url": self.client_url,
            "User-Agent": "OrderBlockSignalBot/1.0",
        })
        try:
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Talasanj HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Talasanj connection failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Talasanj returned an invalid JSON response.")
        return payload

    def prices(self) -> dict[str, Any]:
        payload = self._get("/prices", {"unit": "toman"})
        prices = payload.get("prices") or {}
        gold = prices.get("gram18") or {}
        if not gold.get("price"):
            raise RuntimeError("Talasanj response does not contain prices.gram18.price.")
        return payload

    def intraday_history(self) -> list[tuple[int, float]]:
        payload = self._get("/history", {"symbol": "gram18", "range": "1d"})
        points = payload.get("data") or payload.get("history") or []
        result: list[tuple[int, float]] = []
        for point in points:
            try:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    ts, price = _timestamp_ms(point[0]), float(point[1])
                elif isinstance(point, dict):
                    ts = _timestamp_ms(point.get("timestamp") or point.get("time") or point.get("t"))
                    price = float(point.get("price") or point.get("close"))
                else:
                    continue
                if ts > 0 and price > 0:
                    result.append((ts, price))
            except (TypeError, ValueError, KeyError):
                continue
        return sorted(set(result))


class GoldStore:
    """Small independent SQLite store; does not alter the crypto demo ledger."""

    def __init__(self, database_path: str):
        self.database_path = database_path
        if database_path != ":memory:":
            Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS gold_ticks (
                timestamp_ms INTEGER PRIMARY KEY,
                gram18 REAL NOT NULL,
                usd REAL,
                ounce REAL,
                source TEXT NOT NULL DEFAULT 'talasanj'
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS gold_candles (
                timeframe TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                PRIMARY KEY(timeframe, timestamp_ms)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS gold_signals (
                signal_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )""")

    def _connect(self):
        return sqlite3.connect(self.database_path, timeout=30)

    def add_tick(self, timestamp_ms: int, gram18: float, usd: float | None, ounce: float | None) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO gold_ticks(timestamp_ms,gram18,usd,ounce) VALUES(?,?,?,?)",
                (timestamp_ms, gram18, usd, ounce),
            )
            return cursor.rowcount > 0

    def add_history(self, points: list[tuple[int, float]]) -> int:
        inserted = 0
        with self._connect() as db:
            for ts, price in points:
                cur = db.execute(
                    "INSERT OR IGNORE INTO gold_ticks(timestamp_ms,gram18) VALUES(?,?)",
                    (ts, price),
                )
                inserted += max(cur.rowcount, 0)
        return inserted

    def ticks(self) -> list[tuple[int, float]]:
        with self._connect() as db:
            return [(int(r[0]), float(r[1])) for r in db.execute(
                "SELECT timestamp_ms,gram18 FROM gold_ticks ORDER BY timestamp_ms"
            )]

    def latest_tick(self) -> tuple[int, float, float | None, float | None] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT timestamp_ms,gram18,usd,ounce FROM gold_ticks ORDER BY timestamp_ms DESC LIMIT 1"
            ).fetchone()
            return tuple(row) if row else None

    def save_candle(self, timeframe: str, candle: Candle) -> None:
        with self._connect() as db:
            db.execute("""INSERT OR REPLACE INTO gold_candles
                (timeframe,timestamp_ms,open,high,low,close) VALUES(?,?,?,?,?,?)""",
                (timeframe, candle.timestamp, candle.open, candle.high, candle.low, candle.close))

    def candles(self, timeframe: str) -> list[Candle]:
        with self._connect() as db:
            rows = db.execute("""SELECT timestamp_ms,open,high,low,close FROM gold_candles
                WHERE timeframe=? ORDER BY timestamp_ms""", (timeframe,)).fetchall()
        return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]

    def claim_signal(self, signal: Signal) -> bool:
        key = f"{signal.timestamp}:{signal.side}:{signal.method}"
        with self._connect() as db:
            cur = db.execute("INSERT OR IGNORE INTO gold_signals(signal_key,created_at) VALUES(?,?)",
                             (key, datetime.now(timezone.utc).isoformat()))
            return cur.rowcount > 0


def aggregate_candles(ticks: list[tuple[int, float]], timeframe_ms: int,
                      now_ms: int | None = None) -> list[Candle]:
    """Aggregate sampled prices into closed time bars; current partial bar is excluded."""
    if timeframe_ms <= 0:
        raise ValueError("timeframe_ms must be positive")
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    buckets: dict[int, list[tuple[int, float]]] = {}
    for ts, price in ticks:
        start = (ts // timeframe_ms) * timeframe_ms
        if start + timeframe_ms > now:
            continue
        buckets.setdefault(start, []).append((ts, price))
    result: list[Candle] = []
    for start in sorted(buckets):
        values = sorted(buckets[start])
        prices = [price for _, price in values]
        result.append(Candle(start, prices[0], max(prices), min(prices), prices[-1]))
    return result


class GoldMarketService:
    def __init__(self, api_key: str, database_path: str, strategy_config: StrategyConfig,
                 client_name: str = "OrderBlock Signal Bot", client_url: str = ""):
        self.client = TalasanjClient(api_key, client_name, client_url or
                                     "https://github.com/aliasadollahi3364-ops/orderblock-signal-bot")
        self.store = GoldStore(database_path)
        # The same strategy filters are retained; only the minimum tick is adapted to toman.
        self.strategy = OrderBlockStrategy("GOLD_18K_IRR", replace(strategy_config, min_tick=1.0))
        self.last_processed: dict[str, int] = {}
        self.latest_prices: dict[str, Any] = {}
        self.last_updated_at: str | None = None
        self.last_error: str | None = None
        self.warmed_up = False
        self._bootstrap()

    def _bootstrap(self) -> None:
        try:
            self.store.add_history(self.client.intraday_history())
        except Exception as exc:
            logger.warning("Gold intraday history bootstrap skipped: %s", exc)
        ticks = self.store.ticks()
        bars4 = aggregate_candles(ticks, TIMEFRAMES_MS["4h"])
        bars15 = aggregate_candles(ticks, TIMEFRAMES_MS["15m"])
        events = [(c.timestamp, "4h", c) for c in bars4] + [(c.timestamp, "15m", c) for c in bars15]
        for _, timeframe, candle in sorted(events, key=lambda x: (x[0], 0 if x[1] == "4h" else 1)):
            self._feed_candle(timeframe, candle, emit=False)
            self.store.save_candle(timeframe, candle)
            self.last_processed[timeframe] = candle.timestamp
        self.warmed_up = self.strategy.sma200_4h is not None

    def _feed_candle(self, timeframe: str, candle: Candle, emit: bool = True) -> Signal | None:
        if timeframe == "4h":
            self.strategy.on_4h_close(candle)
            return None
        if timeframe == "15m":
            return self.strategy.on_15m_close(candle) if emit else self.strategy.on_15m_close(candle)
        return None

    def poll_once(self) -> list[Signal]:
        payload = self.client.prices()
        prices = payload.get("prices") or {}
        gold = prices.get("gram18") or {}
        usd = prices.get("usd") or {}
        ounce = prices.get("ounce") or {}
        updated = payload.get("updated_at") or gold.get("updated_at") or datetime.now(timezone.utc).isoformat()
        ts = _timestamp_ms(updated)
        self.latest_prices = {
            "gram18": float(gold["price"]),
            "usd": float(usd["price"]) if usd.get("price") else None,
            "ounce": float(ounce["price"]) if ounce.get("price") else None,
            "unit": payload.get("unit", "toman"),
        }
        self.last_updated_at = str(updated)
        self.last_error = None
        self.store.add_tick(ts, self.latest_prices["gram18"], self.latest_prices["usd"], self.latest_prices["ounce"])
        ticks = self.store.ticks()
        new_bars: dict[str, list[Candle]] = {}
        now_ms = int(time.time() * 1000)
        for timeframe, width in TIMEFRAMES_MS.items():
            bars = aggregate_candles(ticks, width, now_ms)
            new_bars[timeframe] = [bar for bar in bars if bar.timestamp > self.last_processed.get(timeframe, -1)]
            for bar in new_bars[timeframe]:
                self.store.save_candle(timeframe, bar)
                self.last_processed[timeframe] = bar.timestamp

        events = [(c.timestamp, "4h", c) for c in new_bars["4h"]]
        events += [(c.timestamp, "15m", c) for c in new_bars["15m"]]
        signals: list[Signal] = []
        for _, timeframe, candle in sorted(events, key=lambda x: (x[0], 0 if x[1] == "4h" else 1)):
            signal = self._feed_candle(timeframe, candle)
            if signal is not None and self.store.claim_signal(signal):
                signals.append(signal)
        self.warmed_up = self.strategy.sma200_4h is not None
        return signals

    def status_text(self) -> str:
        if not self.latest_prices:
            latest = self.store.latest_tick()
            gold_price = f"{latest[1]:,.0f}" if latest else "هنوز دریافت نشده"
        else:
            gold_price = f"{self.latest_prices['gram18']:,.0f}"
        warm = "آماده (SMA200 چهار‌ساعته موجود است)" if self.warmed_up else "در حال جمع‌آوری تاریخچه؛ سیگنال تا تکمیل داده کافی صادر نمی‌شود"
        updated = self.last_updated_at or "نامشخص"
        error = f"\nخطای آخر: {self.last_error}" if self.last_error else ""
        return (
            "🥇 وضعیت طلای ۱۸ عیار ایران\n"
            f"قیمت هر گرم: {gold_price} تومان\n"
            f"آخرین به‌روزرسانی: {updated}\n"
            f"وضعیت موتور تحلیل: {warm}\n"
            "تایم‌فریم سیگنال: 4H + 15m (مطابق استراتژی اصلی)\n"
            "سیگنال‌ها: فقط تأییدشده | معاملات واقعی: خاموش\n"
            "قیمت‌ها از طلاسنج · https://talasanj.info"
            f"{error}"
        )


def format_gold_signal(signal: Signal) -> str:
    risk = abs(signal.entry - signal.stop)
    direction = 1 if signal.side == "BUY" else -1
    targets = [signal.entry + direction * risk * multiple for multiple in (1, 2, 3)]
    side = "خرید" if signal.side == "BUY" else "فروش"
    entry_time = datetime.fromtimestamp(signal.timestamp / 1000, timezone.utc).astimezone().isoformat()
    return (
        "🥇 سیگنال آزمایشی طلای ۱۸ عیار ایران\n"
        f"جهت: {side} ({signal.method})\n"
        f"ورود: {signal.entry:,.0f} تومان\n"
        f"حد ضرر: {signal.stop:,.0f} تومان\n"
        f"TP1: {targets[0]:,.0f} تومان (1R)\n"
        f"TP2: {targets[1]:,.0f} تومان (2R)\n"
        f"TP3: {targets[2]:,.0f} تومان (3R)\n"
        f"زمان کندل تأیید: {entry_time}\n"
        "خروج: با فعال‌شدن حد ضرر یا اهداف؛ صرفاً هشدار، بدون سفارش واقعی.\n"
        "قیمت‌ها از طلاسنج · https://talasanj.info"
    )
