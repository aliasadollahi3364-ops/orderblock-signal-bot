"""Public-only WEEX market-data adapter with bounded OHLCV/ticker caching.

No credentials or private account endpoints are used.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import ccxt


@dataclass(frozen=True)
class MarketCandidate:
    symbol: str
    quote_volume: float
    spread_pct: float | None
    rank_score: float
    last_price: float | None = None
    tick_size: float = 0.00000001


def _ticker_score(quote_volume: float, bid: float | None, ask: float | None) -> tuple[float | None, float]:
    spread = None
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        mid = (ask + bid) / 2
        spread = ((ask - bid) / mid) * 100 if mid else None
    spread_bonus = 0.0 if spread is None else max(0.0, 1.0 - min(spread, 1.0))
    return spread, quote_volume * (1.0 + 0.05 * spread_bonus)


class WeexMarketData:
    def __init__(self, exchange_id: str = "weex", quote_currency: str = "USDT"):
        exchange_class = getattr(ccxt, exchange_id, None)
        if exchange_class is None:
            raise RuntimeError(f"Installed CCXT does not provide exchange '{exchange_id}'.")
        self.exchange = exchange_class({
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        self.quote_currency = quote_currency.upper()
        self._markets: dict[str, dict[str, Any]] | None = None
        self._ticker_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._ohlcv_cache: dict[tuple[str, str, int], tuple[float, list[list[float]]]] = {}
        self._trades_cache: dict[tuple[str, int], tuple[float, list[dict[str, Any]]]] = {}
        self._orderbook_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}

    def load_markets(self) -> dict[str, dict[str, Any]]:
        if self._markets is None:
            self._markets = self.exchange.load_markets()
        return self._markets

    def _fetch_ticker_cached(self, symbol: str) -> dict[str, Any]:
        """Fetch only the explicitly requested symbol's ticker."""
        now = time.monotonic()
        cached = self._ticker_cache.get(symbol)
        if cached and now < cached[0]:
            return cached[1].copy()
        ticker = self.exchange.fetch_ticker(symbol)
        self._ticker_cache[symbol] = (now + 60.0, ticker)
        return ticker.copy()

    def resolve_manual_symbol(self, requested: str) -> MarketCandidate:
        """Resolve a user symbol to CCXT's canonical active linear USDT swap symbol.

        CCXT commonly represents a USDT-settled perpetual as BTC/USDT:USDT,
        while users naturally enter BTC, BTCUSDT, or BTC/USDT. Match market
        metadata rather than assuming the display symbol has one exact format.
        """
        markets = self.load_markets()
        raw = requested.strip().upper().replace("-", "/").replace(" ", "")
        if not raw:
            raise ValueError("نماد خالی است.")

        # Accept BASE, BASEUSDT, BASE/USDT and BASE/USDT:USDT.
        if "/" in raw:
            base, quote_part = raw.split("/", 1)
            quote = quote_part.split(":", 1)[0]
            if quote and quote != self.quote_currency:
                raise ValueError(f"فقط قراردادهای {self.quote_currency} پشتیبانی می‌شوند.")
        else:
            suffix = self.quote_currency
            base = raw[:-len(suffix)] if raw.endswith(suffix) and len(raw) > len(suffix) else raw

        base = base.strip()
        if not base or not base.replace("_", "").isalnum():
            raise ValueError("فرمت نماد معتبر نیست.")

        matches = []
        for key, item in markets.items():
            symbol = item.get("symbol") or key
            if (
                (item.get("base") or "").upper() == base
                and (item.get("quote") or "").upper() == self.quote_currency
                and item.get("active", True) is not False
                and item.get("swap") is True
                and item.get("linear") is True
                and (not item.get("settle") or item.get("settle").upper() == self.quote_currency)
            ):
                matches.append((symbol, item))

        if not matches:
            requested_display = f"{base}/{self.quote_currency}"
            raise ValueError(
                f"نماد {requested_display} در بازارهای فعال قرارداد دائمی خطی "
                f"{self.quote_currency} صرافی WEEX پیدا نشد."
            )
        if len(matches) > 1:
            # Prefer the canonical USDT-settled contract if the exchange exposes
            # more than one linear contract for the same base/quote.
            matches.sort(key=lambda pair: (pair[1].get("settle") != self.quote_currency, pair[0]))
        symbol, market = matches[0]

        ticker = self._fetch_ticker_cached(symbol) or {}
        last = float(ticker.get("last") or 0) or None
        volume = float(ticker.get("quoteVolume") or 0)
        spread, score = _ticker_score(
            volume,
            float(ticker["bid"]) if ticker.get("bid") else None,
            float(ticker["ask"]) if ticker.get("ask") else None,
        )
        tick_size = self._market_tick_size(market, last)
        return MarketCandidate(symbol, volume, spread, score, last, tick_size)

    def _market_tick_size(self, market: dict[str, Any], last_price: float | None) -> float:
        """Resolve WEEX's minimum price increment for Pine syminfo.mintick parity."""
        info = market.get("info") or {}
        try:
            price_place = info.get("pricePlace")
            end_step = info.get("priceEndStep")
            if price_place is not None and end_step is not None:
                tick = float(end_step) * (10.0 ** -int(price_place))
                if math.isfinite(tick) and tick > 0:
                    return tick
        except (TypeError, ValueError, OverflowError):
            pass
        precision = (market.get("precision") or {}).get("price")
        try:
            if precision is not None:
                mode = getattr(self.exchange, "precisionMode", None)
                if mode == getattr(ccxt, "TICK_SIZE", object()):
                    tick = float(precision)
                elif mode == getattr(ccxt, "DECIMAL_PLACES", object()):
                    tick = 10.0 ** -int(precision)
                elif mode == getattr(ccxt, "SIGNIFICANT_DIGITS", object()) and last_price and last_price > 0:
                    tick = 10.0 ** (math.floor(math.log10(last_price)) - int(precision) + 1)
                else:
                    tick = 0.0
                if math.isfinite(tick) and tick > 0:
                    return tick
        except (TypeError, ValueError, OverflowError):
            pass
        return 0.00000001

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> list[list[float]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        self.load_markets()
        key = (symbol, timeframe, limit)
        now = time.monotonic()
        cached = self._ohlcv_cache.get(key)
        if cached and now < cached[0]:
            return [row[:] for row in cached[1]]
        if limit <= 1000:
            rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        else:
            # Reconstruct enough 15m history to replay the Pine pivots and OB
            # touch state over the same 4h structural window (220 * 16 bars).
            timeframe_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
                            "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}.get(timeframe)
            if timeframe_ms is None:
                raise ValueError(f"Unsupported paginated timeframe: {timeframe}")
            cursor = int(time.time() * 1000) - limit * timeframe_ms
            rows = []
            while len(rows) < limit:
                batch = self.exchange.fetch_ohlcv(
                    symbol, timeframe=timeframe, since=cursor,
                    limit=min(1000, limit - len(rows)),
                )
                if not batch:
                    break
                rows.extend(batch)
                next_cursor = int(batch[-1][0]) + timeframe_ms
                if next_cursor <= cursor or len(batch) < 2:
                    break
                cursor = next_cursor
            rows = sorted({int(row[0]): row for row in rows}.values(), key=lambda row: int(row[0]))[-limit:]
        # 4h structure changes only at a 4h close; 15m entry data is refreshed
        # once per minute. CCXT also enforces the exchange's configured rate limit.
        # Refresh 4H candles promptly: a 15-minute cache can hide a just-confirmed
        # HTF BOS/OB from the 15m strategy during the exact entry window.
        ttl = 30.0 if timeframe == "4h" else 30.0 if timeframe == "15m" else 15.0
        self._ohlcv_cache[key] = (now + ttl, rows)
        return [row[:] for row in rows]


    def fetch_trades(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return normalized public recent trades for the alert-only scanner."""
        if limit < 1:
            raise ValueError("limit must be positive")
        self.load_markets()
        key = (symbol, limit)
        now = time.monotonic()
        cached = self._trades_cache.get(key)
        if cached and now < cached[0]:
            return [trade.copy() for trade in cached[1]]
        raw_trades = self.exchange.fetch_trades(symbol, limit=limit)
        trades = []
        for trade in raw_trades:
            side = str(trade.get("side") or "").lower()
            try:
                volume = float(trade.get("amount") or 0)
                price = float(trade.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if side not in {"buy", "sell"} or volume <= 0 or price <= 0:
                continue
            trades.append({"type": side, "volume": volume, "price": price})
        self._trades_cache[key] = (now + 10.0, trades)
        return [trade.copy() for trade in trades]

    def fetch_orderbook(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        """Return a cached public order-book snapshot; never places or changes orders."""
        if limit < 1:
            raise ValueError("limit must be positive")
        self.load_markets()
        key = (symbol, limit)
        now = time.monotonic()
        cached = self._orderbook_cache.get(key)
        if cached and now < cached[0]:
            return {"bids": [level[:] for level in cached[1].get("bids", [])],
                    "asks": [level[:] for level in cached[1].get("asks", [])]}
        book = self.exchange.fetch_order_book(symbol, limit=limit)
        normalized = {
            "bids": [list(level[:2]) for level in book.get("bids", []) if len(level) >= 2],
            "asks": [list(level[:2]) for level in book.get("asks", []) if len(level) >= 2],
        }
        self._orderbook_cache[key] = (now + 5.0, normalized)
        return {"bids": [level[:] for level in normalized["bids"]],
                "asks": [level[:] for level in normalized["asks"]]}

    def close(self) -> None:
        close = getattr(self.exchange, "close", None)
        if callable(close):
            close()
