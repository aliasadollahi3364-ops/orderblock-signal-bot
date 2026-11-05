"""Public-only WEEX market-data adapter with bounded OHLCV/ticker caching.

No credentials or private account endpoints are used.
"""
from __future__ import annotations

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
        self._ticker_cache: tuple[float, dict[str, Any]] | None = None
        self._ohlcv_cache: dict[tuple[str, str, int], tuple[float, list[list[float]]]] = {}

    def load_markets(self) -> dict[str, dict[str, Any]]:
        if self._markets is None:
            self._markets = self.exchange.load_markets()
        return self._markets

    def _fetch_tickers_cached(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._ticker_cache and now < self._ticker_cache[0]:
            return self._ticker_cache[1]
        tickers = self.exchange.fetch_tickers()
        self._ticker_cache = (now + 60.0, tickers)
        return tickers

    def fetch_usdt_perpetuals(self, limit: int = 500) -> list[MarketCandidate]:
        if limit < 1:
            raise ValueError("limit must be positive")
        markets = self.load_markets()
        tickers = self._fetch_tickers_cached()
        candidates: list[MarketCandidate] = []
        for symbol, market in markets.items():
            if not market.get("active", True) or not market.get("swap") or not market.get("linear"):
                continue
            if (market.get("quote") or "").upper() != self.quote_currency:
                continue
            ticker = tickers.get(symbol) or {}
            quote_volume = ticker.get("quoteVolume")
            if quote_volume is None or float(quote_volume) <= 0:
                base_volume, last = ticker.get("baseVolume"), ticker.get("last")
                if base_volume is None or last is None:
                    continue
                quote_volume = float(base_volume) * float(last)
            quote_volume = float(quote_volume)
            bid = float(ticker["bid"]) if ticker.get("bid") else None
            ask = float(ticker["ask"]) if ticker.get("ask") else None
            spread, score = _ticker_score(quote_volume, bid, ask)
            last_price = float(ticker["last"]) if ticker.get("last") else None
            candidates.append(MarketCandidate(symbol, quote_volume, spread, score, last_price))
        candidates.sort(key=lambda item: item.rank_score, reverse=True)
        return candidates[:limit]

    def resolve_manual_symbol(self, requested: str) -> MarketCandidate:
        """Validate and resolve an explicitly requested active linear USDT swap."""
        markets = self.load_markets()
        normalized = requested.strip().upper().replace("-", "/")
        if not normalized.endswith("/USDT"):
            normalized += "/USDT"
        market = markets.get(normalized)
        if market is None or not market.get("active", True) or not market.get("swap") or not market.get("linear") or (market.get("quote") or "").upper() != self.quote_currency:
            raise ValueError(f"نماد {normalized} در بازارهای فعال قرارداد دائمی USDT صرافی WEEX پیدا نشد.")
        ticker = self._fetch_tickers_cached().get(normalized) or {}
        last = float(ticker.get("last") or 0) or None
        volume = float(ticker.get("quoteVolume") or 0)
        spread, score = _ticker_score(volume, float(ticker["bid"]) if ticker.get("bid") else None, float(ticker["ask"]) if ticker.get("ask") else None)
        return MarketCandidate(normalized, volume, spread, score, last)

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> list[list[float]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        self.load_markets()
        key = (symbol, timeframe, limit)
        now = time.monotonic()
        cached = self._ohlcv_cache.get(key)
        if cached and now < cached[0]:
            return [row[:] for row in cached[1]]
        rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        # 4h structure changes only at a 4h close; 15m entry data is refreshed
        # once per minute. CCXT also enforces the exchange's configured rate limit.
        ttl = 900.0 if timeframe == "4h" else 60.0 if timeframe == "15m" else 15.0
        self._ohlcv_cache[key] = (now + ttl, rows)
        return [row[:] for row in rows]

    def close(self) -> None:
        close = getattr(self.exchange, "close", None)
        if callable(close):
            close()
