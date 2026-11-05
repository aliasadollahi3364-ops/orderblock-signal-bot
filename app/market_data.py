"""Public-only WEEX market-data adapter built on CCXT.

No API keys are accepted and this module exposes no create/cancel order methods.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import ccxt


@dataclass(frozen=True)
class MarketCandidate:
    symbol: str
    quote_volume: float
    spread_pct: float | None
    rank_score: float


def _ticker_score(quote_volume: float, bid: float | None, ask: float | None) -> tuple[float | None, float]:
    spread = None
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        mid = (bid + ask) / 2
        spread = ((ask - bid) / mid) * 100 if mid else None
    # Volume is the primary liquidity proxy; known tight spread adds a modest bonus.
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

    def load_markets(self) -> dict[str, dict[str, Any]]:
        if self._markets is None:
            self._markets = self.exchange.load_markets()
        return self._markets

    def fetch_usdt_perpetuals(self, limit: int = 500) -> list[MarketCandidate]:
        """Return active linear USDT swaps ranked by 24h quote volume and spread."""
        if limit < 1:
            raise ValueError("limit must be positive")
        markets = self.load_markets()
        tickers = self.exchange.fetch_tickers()
        candidates: list[MarketCandidate] = []
        for symbol, market in markets.items():
            if not market.get("active", True):
                continue
            if not market.get("swap") or not market.get("linear"):
                continue
            if (market.get("quote") or "").upper() != self.quote_currency:
                continue
            ticker = tickers.get(symbol) or {}
            quote_volume = ticker.get("quoteVolume")
            if quote_volume is None or float(quote_volume) <= 0:
                base_volume = ticker.get("baseVolume")
                last = ticker.get("last")
                if base_volume is None or last is None:
                    continue
                quote_volume = float(base_volume) * float(last)
            quote_volume = float(quote_volume)
            bid = float(ticker["bid"]) if ticker.get("bid") else None
            ask = float(ticker["ask"]) if ticker.get("ask") else None
            spread, score = _ticker_score(quote_volume, bid, ask)
            candidates.append(MarketCandidate(symbol, quote_volume, spread, score))
        candidates.sort(key=lambda item: item.rank_score, reverse=True)
        return candidates[:limit]

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> list[list[float]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        self.load_markets()
        return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def close(self) -> None:
        close = getattr(self.exchange, "close", None)
        if callable(close):
            close()
