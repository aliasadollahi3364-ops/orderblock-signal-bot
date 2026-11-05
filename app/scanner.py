"""Two-stage WEEX scanner: rank the market universe, then evaluate confirmed OBs.

The scanner is deliberately demo-only. It fetches public candles, never exchange
account data, and never submits orders.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.demo import Side
from app.market_data import MarketCandidate, WeexMarketData
from app.runtime import runtime
from app.strategy import Candle, OrderBlockStrategy, Signal, StrategyConfig

log = logging.getLogger(__name__)
MS_15M = 15 * 60 * 1000
MS_4H = 4 * 60 * 60 * 1000


@dataclass(frozen=True)
class ScanSummary:
    markets_seen: int
    ob_candidates: int
    entry_candidates: int
    signals: int
    errors: int


def _to_candles(rows: list[list[float]]) -> list[Candle]:
    """Convert exchange OHLCV rows, ignoring malformed or invalid candles.

    Some WEEX markets can return zero, non-finite, or internally inconsistent
    OHLC values. One bad row must not abort screening for an entire symbol.
    """
    result = []
    for row in rows:
        if len(row) < 6:
            continue
        try:
            timestamp = int(row[0])
            open_price, high, low, close, volume = map(float, row[1:6])
            values = (open_price, high, low, close, volume)
            if not all(math.isfinite(value) for value in values):
                continue
            if min(open_price, high, low, close) <= 0 or volume < 0:
                continue
            if high < max(open_price, close, low) or low > min(open_price, close, high):
                continue
            result.append(Candle(
                timestamp=timestamp, open=open_price, high=high,
                low=low, close=close, volume=volume,
            ))
        except (TypeError, ValueError, OverflowError):
            continue
    return sorted({bar.timestamp: bar for bar in result}.values(), key=lambda bar: bar.timestamp)


def _ordered_feed(engine: OrderBlockStrategy, bars4: list[Candle],
                  bars15: list[Candle]) -> Signal | None:
    """Replay closed 4h/15m candles by close-time, with 4h first on ties."""
    events: list[tuple[int, int, Candle]] = []
    events.extend((bar.timestamp + MS_4H, 0, bar) for bar in bars4)
    events.extend((bar.timestamp + MS_15M, 1, bar) for bar in bars15)
    events.sort(key=lambda event: (event[0], event[1]))
    latest_signal = None
    now_ms = int(time.time() * 1000)
    for close_time, kind, bar in events:
        if close_time > now_ms:
            continue
        if kind == 0:
            engine.on_4h_close(bar)
        else:
            signal = engine.on_15m_close(bar)
            if signal is not None:
                latest_signal = signal
    completed_15m = [b for b in bars15 if b.timestamp + MS_15M <= now_ms]
    if not completed_15m or latest_signal is None:
        return None
    return latest_signal if latest_signal.timestamp == completed_15m[-1].timestamp else None


def _active_ob_distance(engine: OrderBlockStrategy, price: float) -> float | None:
    zones = [ob for ob in engine.order_blocks if ob.active and not ob.used]
    if not zones:
        return None

    def distance(ob):
        if ob.bottom <= price <= ob.top:
            return 0.0
        return min(abs(price - ob.top), abs(price - ob.bottom)) / max(price, 1e-12)

    return min(distance(ob) for ob in zones)


class AutomaticScanner:
    def __init__(
        self,
        market_data: WeexMarketData,
        strategy_config: StrategyConfig | None = None,
        universe_limit: int = 500,
        candidate_limit: int = 60,
        on_signal: Callable[[Signal], None] | None = None,
        on_trade_event: Callable[[dict], None] | None = None,
    ):
        if universe_limit < 1 or candidate_limit < 1:
            raise ValueError("Scanner limits must be positive.")
        self.market_data = market_data
        self.strategy_config = strategy_config or StrategyConfig()
        self.universe_limit = universe_limit
        self.candidate_limit = candidate_limit
        self.on_signal = on_signal
        self.on_trade_event = on_trade_event
        self._last_demo_bar: dict[str, int] = {}
        self._last_signal_bar: dict[str, int] = {}
        self._selected: list[str] = []

    def scan_once(self) -> ScanSummary:
        runtime.scanner_state = "SCANNING_MARKETS"
        runtime.last_scan_at = datetime.now(timezone.utc)
        errors = 0
        try:
            markets = self.market_data.fetch_usdt_perpetuals(self.universe_limit)
        except Exception:
            runtime.scanner_state = "MARKET_DATA_ERROR"
            log.exception("Failed to load WEEX USDT perpetual universe")
            return ScanSummary(0, 0, 0, 0, 1)

        ob_ranked: list[tuple[float, MarketCandidate]] = []
        market_cache: dict[str, tuple[list[Candle], list[Candle], float]] = {}
        for market in markets:
            try:
                bars4 = _to_candles(self.market_data.fetch_ohlcv(market.symbol, "4h", 220))
                if len(bars4) < 30:
                    continue
                htf_engine = OrderBlockStrategy(market.symbol, self.strategy_config)
                now_ms = int(time.time() * 1000)
                for bar in bars4:
                    if bar.timestamp + MS_4H <= now_ms:
                        htf_engine.on_4h_close(bar)
                last_price = market.last_price
                if last_price is None or last_price <= 0:
                    continue
                distance = _active_ob_distance(htf_engine, last_price)
                if distance is None:
                    continue
                score = market.rank_score / (1.0 + distance * 100.0)
                ob_ranked.append((score, market))
                market_cache[market.symbol] = (bars4, [], last_price)
            except Exception:
                errors += 1
                log.exception("4h OB screening failed for %s", market.symbol)

        ob_ranked.sort(key=lambda item: item[0], reverse=True)
        selected_markets = [market for _, market in ob_ranked[:self.candidate_limit]]
        signals: list[Signal] = []
        runtime.scanner_state = "SCANNING_15M"
        for market in selected_markets:
            try:
                bars4 = market_cache[market.symbol][0]
                bars15 = _to_candles(self.market_data.fetch_ohlcv(market.symbol, "15m", 320))
                engine = OrderBlockStrategy(market.symbol, self.strategy_config)
                signal = _ordered_feed(engine, bars4, bars15)
                if signal is not None and self._last_signal_bar.get(signal.symbol) != signal.timestamp:
                    self._last_signal_bar[signal.symbol] = signal.timestamp
                    self._last_demo_bar[signal.symbol] = signal.timestamp
                    signals.append(signal)
                    if self.on_signal:
                        self.on_signal(signal)
            except Exception:
                errors += 1
                log.exception("15m strategy evaluation failed for %s", market.symbol)

        for position in list(runtime.account.open_positions):
            try:
                rows = self.market_data.fetch_ohlcv(position.symbol, "15m", 2)
                bars = _to_candles(rows)
                if not bars:
                    continue
                now_ms = int(time.time() * 1000)
                bar = next((item for item in reversed(bars)
                            if item.timestamp + MS_15M <= now_ms), None)
                if bar is None or self._last_demo_bar.get(position.symbol) == bar.timestamp:
                    continue
                self._last_demo_bar[position.symbol] = bar.timestamp
                events = runtime.account.process_candle(
                    high=bar.high, low=bar.low, symbol=position.symbol
                )
                for event in events:
                    record = {
                        "symbol": position.symbol,
                        "side": position.side.value,
                        "time": datetime.now(timezone.utc).isoformat(),
                        **event,
                        "realized_pnl": position.realized_pnl,
                    }
                    runtime.closed_trade_events.append(record)
                    if self.on_trade_event:
                        self.on_trade_event(record)
                if events:
                    runtime.persist_demo_state()
            except Exception:
                errors += 1
                log.exception("Demo position management failed for %s", position.symbol)

        self._selected = [market.symbol for market in selected_markets]
        runtime.selected_symbols = list(self._selected)
        runtime.signal_count += len(signals)
        runtime.last_scan_at = datetime.now(timezone.utc)
        runtime.scanner_state = "RUNNING" if not errors else "RUNNING_WITH_ERRORS"
        return ScanSummary(len(markets), len(ob_ranked), len(selected_markets), len(signals), errors)

    def run_forever(self, interval_seconds: int = 60) -> None:
        while True:
            started = time.monotonic()
            summary = self.scan_once()
            log.info("Scan completed: %s", summary)
            time.sleep(max(1, interval_seconds - (time.monotonic() - started)))


def open_demo_from_signal(signal: Signal):
    """Open a single fixed-size internal demo position; never touches exchange orders."""
    side = Side.LONG if signal.side == "BUY" else Side.SHORT
    try:
        position = runtime.account.open_position(signal.symbol, side, signal.entry, signal.stop)
        runtime.persist_demo_state()
        return position
    except ValueError as exc:
        log.info("Demo entry skipped for %s: %s", signal.symbol, exc)
        return None
