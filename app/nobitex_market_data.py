"""Read-only Nobitex market-data adapter.

Manual symbol mode works without credentials using public market statistics and
OHLC endpoints. The personalized margin-market catalog is optional and requires
a user token; without it, margin eligibility cannot be independently verified.
No order-placement methods are implemented.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

BASE_URL = "https://apiv2.nobitex.ir"


@dataclass(frozen=True)
class NobitexMarketCandidate:
    symbol: str
    quote_volume: float
    spread_pct: float | None
    rank_score: float
    last_price: float | None = None
    margin_eligibility_confirmed: bool = False


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
    """Public-data adapter; token is optional and used only for margin catalog."""

    RESOLUTIONS = {
        "1m": "1", "5m": "5", "15m": "15", "30m": "30",
        "1h": "60", "3h": "180", "4h": "240", "6h": "360",
        "12h": "720", "1d": "D",
    }

    def __init__(self, api_token: str = "", timeout: float = 15.0):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "orderblock-signal-bot/1.0",
        })
        if api_token and api_token.strip():
            self.session.headers["Authorization"] = f"Token {api_token.strip()}"
        self.has_token = bool(api_token and api_token.strip())
        self.timeout = timeout
        self._margin_cache: tuple[float, list[MarginMarket]] | None = None
        self._stats_cache: tuple[float, dict[str, Any]] | None = None
        self._ohlcv_cache: dict[tuple[str, str, int], tuple[float, list[list[float]]]] = {}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(f"{BASE_URL}{path}", params=params, timeout=self.timeout)
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
        """Fetch the personalized margin catalog; Nobitex documents this as token-authenticated."""
        if not self.has_token:
            raise RuntimeError(
                "فهرست رسمی بازارهای تعهدی نوبیتکس به توکن نیاز دارد؛ "
                "بدون توکن از افزودن دستی نماد و داده عمومی استفاده می‌شود."
            )
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
            raw_quote = str(item.get("dstCurrency") or "").upper()
            quote = "IRT" if raw_quote == "RLS" else raw_quote
            buy_enabled, sell_enabled = item.get("buyEnabled") is True, item.get("sellEnabled") is True
            if not base or not quote or not (buy_enabled or sell_enabled):
                continue
            try:
                leverage = float(item.get("maxLeverage") or 1)
                fee = float(item.get("positionFeeRate") or 0)
            except (TypeError, ValueError):
                continue
            markets.append(MarginMarket(str(symbol).upper(), base, quote, buy_enabled, sell_enabled, leverage, fee))
        markets.sort(key=lambda item: item.symbol)
        self._margin_cache = (now + 900.0, markets)
        return list(markets)

    def fetch_market_stats(self, force: bool = False) -> dict[str, Any]:
        """Public 24-hour market statistics; no API token is required."""
        now = time.monotonic()
        if not force and self._stats_cache and now < self._stats_cache[0]:
            return dict(self._stats_cache[1])
        payload = self._get("/market/stats")
        stats = payload.get("stats")
        if not isinstance(stats, dict):
            raise RuntimeError("Malformed Nobitex market-stats response.")
        self._stats_cache = (now + 30.0, stats)
        return dict(stats)

    @staticmethod
    def _stats_key(symbol: str) -> str:
        raw = symbol.strip().upper().replace("/", "").replace("-", "")
        if raw.endswith("IRT"):
            return f"{raw[:-3].lower()}-rls"
        if raw.endswith("USDT"):
            return f"{raw[:-4].lower()}-usdt"
        raise ValueError("نماد را به شکل BTCUSDT یا BTCIRT وارد کن.")

    def resolve_manual_symbol(self, requested: str) -> NobitexMarketCandidate:
        """Resolve a manually entered public market symbol without claiming margin eligibility."""
        raw = requested.strip().upper().replace("/", "").replace("-", "").replace(" ", "")
        if not raw:
            raise ValueError("نماد خالی است.")
        if not raw.endswith(("USDT", "IRT")):
            # A base-only entry prefers USDT, then IRT.
            candidates = [raw + "USDT", raw + "IRT"]
        else:
            candidates = [raw]
        stats = self.fetch_market_stats()
        selected_symbol, item = "", None
        for candidate in candidates:
            try:
                key = self._stats_key(candidate)
            except ValueError:
                continue
            found = stats.get(key)
            if isinstance(found, dict) and found.get("isClosed") is not True:
                selected_symbol, item = candidate, found
                break
        if item is None:
            raise ValueError(f"بازار عمومی فعال برای {requested} در نوبیتکس پیدا نشد.")
        try:
            last = float(item.get("latest") or item.get("mark") or 0) or None
            volume = float(item.get("volumeDst") or 0)
            bid = float(item.get("bestBuy") or 0)
            ask = float(item.get("bestSell") or 0)
        except (TypeError, ValueError):
            raise RuntimeError(f"داده آماری بازار {selected_symbol} نامعتبر است.")
        spread = ((ask - bid) / ((ask + bid) / 2) * 100) if bid > 0 and ask >= bid else None
        rank = volume * (1.0 + 0.05 * max(0.0, 1.0 - min(spread or 1.0, 1.0)))
        return NobitexMarketCandidate(selected_symbol, volume, spread, rank, last, False)

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
            "symbol": canonical, "resolution": self.RESOLUTIONS[timeframe],
            "to": int(time.time()), "countback": limit,
        })
        if payload.get("s") == "no_data":
            return []
        if payload.get("s") != "ok":
            raise RuntimeError(f"Unexpected Nobitex OHLC response for {canonical}: {payload.get('s')}")
        columns = [payload.get(name) for name in ("t", "o", "h", "l", "c", "v")]
        if any(not isinstance(column, list) for column in columns):
            raise RuntimeError(f"Malformed OHLC response for {canonical}.")
        size = min(map(len, columns))
        rows = []
        for index in range(size):
            try:
                rows.append([
                    int(columns[0][index]) * 1000, float(columns[1][index]),
                    float(columns[2][index]), float(columns[3][index]),
                    float(columns[4][index]), float(columns[5][index]),
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

    def close(self) -> None:
        self.session.close()
