"""Two-stage manual-symbol scanner: rank selected markets, then evaluate confirmed OBs.

The scanner is deliberately demo-only. It fetches public candles, never exchange
account data, and never submits orders.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.demo import Side
from app.market_data import MarketCandidate
from app.pending_limits import PendingStatus
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

    Some Exchange markets can return zero, non-finite, or internally inconsistent
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
    """Replay closed candles by close-time; process the final 15m bar before the new 4h close on boundary ties to avoid HTF lookahead."""
    events: list[tuple[int, int, Candle]] = []
    events.extend((bar.timestamp + MS_4H, 1, bar) for bar in bars4)
    events.extend((bar.timestamp + MS_15M, 0, bar) for bar in bars15)
    events.sort(key=lambda event: (event[0], event[1]))
    latest_signal = None
    now_ms = int(time.time() * 1000)
    for close_time, kind, bar in events:
        if close_time > now_ms:
            continue
        if kind == 1:
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
        market_data: Any,
        strategy_config: StrategyConfig | None = None,
        universe_limit: int = 500,
        candidate_limit: int = 60,
        on_signal: Callable[[Signal], None] | None = None,
        on_trade_event: Callable[[dict], None] | None = None,
        on_pending_event: Callable[[dict], None] | None = None,
    ):
        if universe_limit < 1 or candidate_limit < 1:
            raise ValueError("Scanner limits must be positive.")
        self.market_data = market_data
        self.strategy_config = strategy_config or StrategyConfig()
        self.universe_limit = universe_limit
        self.candidate_limit = candidate_limit
        self.on_signal = on_signal
        self.on_trade_event = on_trade_event
        self.on_pending_event = on_pending_event
        self._last_demo_bar: dict[str, int] = {}
        self._last_signal_bar: dict[str, int] = {}
        self._selected: list[str] = []

    def _cancel_pending_after_position_close(self, symbol: str, timestamp: int) -> None:
        cancelled = runtime.pending_limits.cancel(
            symbol, timestamp, "پوزیشن دمو بسته شد؛ لیمیت باقیمانده دیگر معتبر نیست"
        )
        if not cancelled:
            return
        runtime.persist_pending_limits()
        event_time = datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()
        for item in cancelled:
            if self.on_pending_event:
                self.on_pending_event({
                    "symbol": symbol, "side": item.side,
                    "type": "LIMIT_CANCELLED", "leg": item.leg_number,
                    "price": item.price, "time": event_time,
                    "reason": item.reason,
                })

    def scan_once(self) -> ScanSummary:
        runtime.scanner_state = "SCANNING_MARKETS"
        runtime.last_scan_at = datetime.now(timezone.utc)
        errors = 0
        # Read the latest persisted Telegram settings once per scan cycle.
        strategy_config = runtime.strategy_config or self.strategy_config
        # Manual-only mode: never download or select the exchange-wide market universe.
        # The scanner evaluates only symbols explicitly saved through Telegram /add.
        manual_markets: list[Any] = []
        for requested in list(runtime.manual_symbols):
            try:
                manual_markets.append(self.market_data.resolve_manual_symbol(requested))
            except Exception:
                errors += 1
                log.warning("Manual symbol unavailable on configured exchange: %s", requested)
        markets = manual_markets

        # Pending limit setups are monitored independently of the current OB
        # ranking so a still-valid setup is not abandoned if its symbol drops
        # out of today's candidate shortlist.
        now_ms = int(time.time() * 1000)
        for symbol, setup in list(runtime.pending_limits.setups.items()):
            if not setup.active:
                continue
            try:
                rows = self.market_data.fetch_ohlcv(symbol, "15m", 2)
                bars = _to_candles(rows)
                bar = next((item for item in reversed(bars)
                            if item.timestamp + MS_15M <= now_ms), None)
                if bar is None:
                    continue
                pending_events = runtime.pending_limits.process_candle(
                    symbol, bar.timestamp + MS_15M, bar.high, bar.low, bar.close
                )
                if not pending_events:
                    continue
                runtime.persist_pending_limits()
                filled_any = False
                for pending_event in pending_events:
                    event_time = datetime.fromtimestamp(
                        (bar.timestamp + MS_15M) / 1000, timezone.utc
                    ).isoformat()
                    if pending_event.status == "FILLED":
                        side = Side.LONG if pending_event.side == "BUY" else Side.SHORT
                        try:
                            position = runtime.account.add_limit_fill(
                                symbol, side, pending_event.price, setup.stop,
                                opened_at=event_time,
                            )
                            filled_any = True
                            runtime.closed_trade_events.append({
                                "symbol": symbol, "side": side.value, "type": "LIMIT_FILLED",
                                "leg": pending_event.leg_number, "price": pending_event.price,
                                "time": event_time, "realized_pnl": position.realized_pnl,
                            })
                            runtime.persist_demo_state()
                            record = {
                                "symbol": symbol, "side": pending_event.side,
                                "type": "LIMIT_FILLED", "leg": pending_event.leg_number,
                                "price": pending_event.price, "time": event_time,
                                "realized_pnl": position.realized_pnl,
                            }
                        except ValueError as exc:
                            # Never report a simulated fill that the demo ledger
                            # could not accept; convert this leg to an explicit cancel.
                            leg = next(x for x in setup.limits if x.number == pending_event.leg_number)
                            leg.status = PendingStatus.CANCELLED
                            leg.cancelled_at = bar.timestamp
                            leg.cancel_reason = f"ثبت دمو ناموفق: {exc}"
                            runtime.persist_pending_limits()
                            record = {
                                "symbol": symbol, "side": pending_event.side,
                                "type": "LIMIT_CANCELLED", "leg": pending_event.leg_number,
                                "price": pending_event.price, "time": event_time,
                                "reason": leg.cancel_reason,
                            }
                    else:
                        record = {
                            "symbol": symbol, "side": pending_event.side,
                            "type": "LIMIT_CANCELLED", "leg": pending_event.leg_number,
                            "price": pending_event.price, "time": event_time,
                            "reason": pending_event.reason,
                        }
                    if self.on_pending_event:
                        self.on_pending_event(record)

                if filled_any:
                    # Evaluate the fill candle once; stop has priority over
                    # targets when OHLC cannot establish the intrabar sequence.
                    position = runtime.account.positions.get(symbol)
                    if position is not None and not position.closed:
                        trade_events = runtime.account.process_candle(
                            high=bar.high, low=bar.low, symbol=symbol
                        )
                        for event in trade_events:
                            record = {
                                "symbol": symbol, "side": position.side.value,
                                "time": datetime.fromtimestamp(
                                    (bar.timestamp + MS_15M) / 1000, timezone.utc
                                ).isoformat(),
                                **event, "realized_pnl": position.realized_pnl,
                            }
                            runtime.closed_trade_events.append(record)
                            if self.on_trade_event:
                                self.on_trade_event(record)
                        runtime.persist_demo_state()
                        if position.closed:
                            self._cancel_pending_after_position_close(
                                symbol, bar.timestamp + MS_15M
                            )
                    self._last_demo_bar[symbol] = bar.timestamp
            except Exception:
                errors += 1
                log.exception("Pending limit processing failed for %s", symbol)

        ob_ranked: list[tuple[float, MarketCandidate]] = []
        market_cache: dict[str, tuple[list[Candle], list[Candle], float]] = {}
        for market in markets:
            try:
                bars4 = _to_candles(self.market_data.fetch_ohlcv(market.symbol, "4h", 220))
                if len(bars4) < 30:
                    continue
                htf_engine = OrderBlockStrategy(market.symbol, strategy_config)
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
        # Only manually added symbols with an active, unused 4H order block
        # proceed to 15m signal evaluation. No automatic candidates are added.
        selected_markets = [market for _, market in ob_ranked]
        signals: list[Signal] = []
        runtime.scanner_state = "SCANNING_15M"
        for market in selected_markets:
            try:
                bars4 = market_cache[market.symbol][0]
                bars15 = _to_candles(self.market_data.fetch_ohlcv(market.symbol, "15m", 320))
                engine = OrderBlockStrategy(market.symbol, strategy_config)
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
                        "time": datetime.fromtimestamp((bar.timestamp + MS_15M) / 1000, timezone.utc).isoformat(),
                        **event,
                        "realized_pnl": position.realized_pnl,
                    }
                    runtime.closed_trade_events.append(record)
                    if self.on_trade_event:
                        self.on_trade_event(record)
                if events:
                    runtime.persist_demo_state()
                if position.closed:
                    self._cancel_pending_after_position_close(
                        position.symbol, bar.timestamp + MS_15M
                    )
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
        entry_time = datetime.fromtimestamp((signal.timestamp + MS_15M) / 1000, timezone.utc).isoformat()
        position = runtime.account.open_position(signal.symbol, side, signal.entry, signal.stop, opened_at=entry_time)
        runtime.persist_demo_state()
        return position
    except ValueError as exc:
        log.info("Demo entry skipped for %s: %s", signal.symbol, exc)
        return None
