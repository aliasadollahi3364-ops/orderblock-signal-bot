"""Nobitex market-data adapter.

Uses only public market-data endpoints plus the read-only margin-market
catalog endpoint. This module intentionally exposes no order-placement API.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests


BASE_URL = "https://apiv2.nobitex.ir"


@dataclass(frozen=True)
class MarginMarket:
    symbol: str
    base: str
    quote: str
    buy_enabled: bool
    sell_enabled: bool
    max_leverage: float
    position_fee_rate: float


class NobitexMarketData:
    """Read-only Nobitex adapter for margin-market discovery and candles."""

    RESOLUTIONS = {
        "1m": "1", "5m": "5", "15m": "15", "30m": "30",
        "1h": "60", "3h": "180", "4h": "240", "6h": "360",
        "12h": "720", "1d": "D",
    }

    def __init__(self, api_token: str, timeout: float = 15.0):
        if not api_token or not api_token.strip():
            raise ValueError(
                "NOBITEX_API_TOKEN is required to read the personalized margin-market list."
            )
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {api_token.strip()}",
            "Accept": "application/json",
            "User-Agent": "orderblock-signal-bot/1.0",
        })
        self.timeout = timeout
        self._margin_cache: tuple[float, list[MarginMarket]] | None = None
        self._ohlcv_cache: dict[tuple[str, str, int], tuple[float, list[list[float]]]] = {}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(
            f"{BASE_URL}{path}", params=params, timeout=self.timeout
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected Nobitex response for {path}.")
        if payload.get("status") in {"failed", "error"} or payload.get("s") == "error":
            raise RuntimeError(
                f"Nobitex API rejected {path}: "
                f"{payload.get('message') or payload.get('errmsg') or payload.get('code')}"
            )
        return payload

    def fetch_margin_markets(self, force: bool = False) -> list[MarginMarket]:
        """Return every market with at least one enabled margin direction."""
        now = time.monotonic()
        if not force and self._margin_cache and now < self._margin_cache[0]:
            return list(self._margin_cache[1])

        payload = self._get("/margin/markets/list")
        raw_markets = payload.get("markets")
        if not isinstance(raw_markets, dict):
            raise RuntimeError("Nobitex margin-market response has no markets object.")

        markets: list[MarginMarket] = []
        for symbol, item in raw_markets.items():
            if not isinstance(item, dict):
                continue
            base = str(item.get("srcCurrency") or "").upper()
            quote = str(item.get("dstCurrency") or "").upper()
            buy_enabled = item.get("buyEnabled") is True
            sell_enabled = item.get("sellEnabled") is True
            if not base or not quote or not (buy_enabled or sell_enabled):
                continue
            try:
                max_leverage = float(item.get("maxLeverage") or 1)
                fee_rate = float(item.get("positionFeeRate") or 0)
            except (TypeError, ValueError):
                continue
            markets.append(MarginMarket(
                symbol=str(symbol).upper(),
                base=base,
                quote=quote,
                buy_enabled=buy_enabled,
                sell_enabled=sell_enabled,
                max_leverage=max_leverage,
                position_fee_rate=fee_rate,
            ))

        markets.sort(key=lambda market: market.symbol)
        self._margin_cache = (now + 900.0, markets)
        return list(markets)

    def resolve_manual_symbol(self, requested: str) -> MarginMarket:
        raw = requested.strip().upper().replace("/", "").replace("-", "")
        if not raw:
            raise ValueError("نماد خالی است.")
        matches = [
            market for market in self.fetch_margin_markets()
            if market.symbol == raw or market.base == raw
        ]
        if not matches:
            raise ValueError(f"نماد {requested} در فهرست بازارهای تعهدی فعال نوبیتکس نیست.")
        if len(matches) > 1:
            usdt = [market for market in matches if market.quote == "USDT"]
            matches = usdt or matches
        if len(matches) > 1:
            raise ValueError(f"برای {requested} چند بازار تعهدی وجود دارد؛ نماد کامل را وارد کن.")
        return matches[0]

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> list[list[float]]:
        if timeframe not in self.RESOLUTIONS:
            raise ValueError(f"Unsupported Nobitex timeframe: {timeframe}")
        if limit < 1 or limit > 500:
            raise ValueError("Nobitex returns at most 500 candles per OHLC request.")
        canonical = symbol.strip().upper().replace("/", "").replace("-", "")
        key = (canonical, timeframe, limit)
        now = time.monotonic()
        cached = self._ohlcv_cache.get(key)
        if cached and now < cached[0]:
            return [row[:] for row in cached[1]]

        payload = self._get("/market/udf/history", {
            "symbol": canonical,
            "resolution": self.RESOLUTIONS[timeframe],
            "to": int(time.time()),
            "countback": limit,
        })
        if payload.get("s") != "ok":
            if payload.get("s") == "no_data":
                return []
            raise RuntimeError(f"Unexpected Nobitex OHLC response for {canonical}: {payload.get('s')}")

        columns = [payload.get(key) for key in ("t", "o", "h", "l", "c", "v")]
        if any(not isinstance(column, list) for column in columns):
            raise RuntimeError(f"Malformed OHLC response for {canonical}.")
        size = min(map(len, columns))
        rows = []
        for index in range(size):
            try:
                rows.append([
                    int(columns[0][index]) * 1000,
                    float(columns[1][index]),
                    float(columns[2][index]),
                    float(columns[3][index]),
                    float(columns[4][index]),
                    float(columns[5][index]),
                ])
            except (TypeError, ValueError, OverflowError):
                continue
        rows.sort(key=lambda row: row[0])
        ttl = 900.0 if timeframe == "4h" else 60.0 if timeframe in {"15m", "1h"} else 15.0
        self._ohlcv_cache[key] = (now + ttl, rows)
        return [row[:] for row in rows]

    def fetch_trades(self, symbol: str) -> list[dict[str, Any]]:
        canonical = symbol.strip().upper().replace("/", "").replace("-", "")
        payload = self._get(f"/v2/trades/{canonical}")
        trades = payload.get("trades")
        if not isinstance(trades, list):
            raise RuntimeError(f"Malformed trades response for {canonical}.")
        return trades

    def fetch_orderbook(self, symbol: str) -> dict[str, Any]:
        canonical = symbol.strip().upper().replace("/", "").replace("-", "")
        payload = self._get(f"/v3/orderbook/{canonical}")
        if payload.get("status") != "ok":
            raise RuntimeError(f"Malformed orderbook response for {canonical}.")
        return payload

    def fetch_market_stats(self) -> dict[str, Any]:
        payload = self._get("/market/stats")
        stats = payload.get("stats")
        if not isinstance(stats, dict):
            raise RuntimeError("Malformed Nobitex market-stats response.")
        return stats

    def close(self) -> None:
        self.session.close()
