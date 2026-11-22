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
from app.strategy import Candle, EarlyWarning, OrderBlockStrategy, Signal, StrategyConfig

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
                  bars15: list[Candle]) -> tuple[Signal | None, EarlyWarning | None]:
    """Replay closed candles by close-time; process the final 15m bar before the new 4h close on boundary ties to avoid HTF lookahead."""
    events: list[tuple[int, int, Candle]] = []
    events.extend((bar.timestamp + MS_4H, 1, bar) for bar in bars4)
    events.extend((bar.timestamp + MS_15M, 0, bar) for bar in bars15)
    events.sort(key=lambda event: (event[0], event[1]))
    latest_signal = None
    latest_warning = None
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
            if engine.last_early_warning is not None:
                latest_warning = engine.last_early_warning
    completed_15m = [b for b in bars15 if b.timestamp + MS_15M <= now_ms]
    if not completed_15m:
        return None, None
    latest_bar_timestamp = completed_15m[-1].timestamp
    current_signal = (latest_signal if latest_signal is not None and
                      latest_signal.timestamp == latest_bar_timestamp else None)
    current_warning = (latest_warning if latest_warning is not None and
                       latest_warning.timestamp == latest_bar_timestamp else None)
    return current_signal, current_warning


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
        on_early_warning: Callable[[EarlyWarning], None] | None = None,
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
        self.on_early_warning = on_early_warning
        self.on_trade_event = on_trade_event
        self.on_pending_event = on_pending_event
        self._last_demo_bar: dict[str, int] = {}
        self._last_signal_bar: dict[str, int] = {}
        self._last_early_warning_bar: dict[str, int] = {}
        self._selected: list[str] = []

    def _cancel_remaining_pending(self, symbol: str, timestamp: int, reason: str,
                                  *, persist: bool = True, notify: bool = True) -> list[Any]:
        """Cancel unfilled legs; callers may defer persistence/notifications for an atomic exit snapshot."""
        cancelled = runtime.pending_limits.cancel(symbol, timestamp, reason)
        if not cancelled:
            return []
        if persist:
            runtime.persist_pending_limits()
        if notify:
            self._notify_pending_cancellations(symbol, timestamp, cancelled)
        return cancelled

    def _notify_pending_cancellations(self, symbol: str, timestamp: int,
                                      cancelled: list[Any]) -> None:
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

        # Replay every newly closed 15m candle for pending setups in chronological
        # order. Looking only at the latest candle can miss a limit touch when
        # scans are delayed or the process was temporarily offline.
        now_ms = int(time.time() * 1000)
        for symbol, setup in list(runtime.pending_limits.setups.items()):
            position = runtime.account.positions.get(symbol)
            if not setup.active and (position is None or position.closed):
                continue
            try:
                lookback = max(40, setup.expires_after_bars + 3)
                rows = self.market_data.fetch_ohlcv(symbol, "15m", lookback)
                bars = _to_candles(rows)
                for bar in bars:
                    close_time = bar.timestamp + MS_15M
                    event_time = datetime.fromtimestamp(
                        close_time / 1000, timezone.utc
                    ).isoformat()
                    if close_time > now_ms:
                        continue
                    if setup.last_processed_bar is not None and close_time <= setup.last_processed_bar:
                        continue
                    if close_time <= setup.created_at:
                        continue

                    pending_events = runtime.pending_limits.process_candle(
                        symbol, close_time, bar.high, bar.low, bar.close
                    )
                    if pending_events:
                        runtime.persist_pending_limits()
                    filled_this_bar = any(
                        event.status == "FILLED" for event in pending_events
                    )
                    for pending_event in pending_events:
                        event_time = datetime.fromtimestamp(
                            close_time / 1000, timezone.utc
                        ).isoformat()
                        if pending_event.status == "FILLED":
                            side = Side.LONG if pending_event.side == "BUY" else Side.SHORT
                            try:
                                position = runtime.account.add_limit_fill(
                                    symbol, side, pending_event.price, setup.stop,
                                    opened_at=event_time,
                                    leg_number=pending_event.leg_number,
                                    risk_reward=runtime.strategy_config.risk_reward,
                                )
                                runtime.closed_trade_events.append({
                                    "symbol": symbol, "side": side.value, "type": "LIMIT_FILLED",
                                    "leg": pending_event.leg_number, "price": pending_event.price,
                                    "time": event_time, "realized_pnl": position.realized_pnl,
                                    "average_entry": position.entry, "margin_allocated": position.margin_allocated,
                                    "tp1": position.tp1, "tp2": position.tp2, "tp3": position.tp3,
                                })
                                runtime.persist_demo_state()
                                record = {
                                    "symbol": symbol, "side": pending_event.side,
                                    "type": "LIMIT_FILLED", "leg": pending_event.leg_number,
                                    "price": pending_event.price, "time": event_time,
                                    "realized_pnl": position.realized_pnl,
                                    "average_entry": position.entry, "margin_allocated": position.margin_allocated,
                                    "tp1": position.tp1, "tp2": position.tp2, "tp3": position.tp3,
                                }
                            except ValueError as exc:
                                # Do not announce a fill the demo ledger rejected.
                                leg = next(x for x in setup.limits if x.number == pending_event.leg_number)
                                leg.status = PendingStatus.CANCELLED
                                leg.cancelled_at = close_time
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

                    # A filled leg becomes a demo position at this candle's
                    # limit price. Manage it on this and each subsequent closed
                    # candle, with the demo ledger's conservative stop priority.
                    position = runtime.account.positions.get(symbol)
                    if position is not None and not position.closed:
                        trade_events = runtime.account.process_candle(
                            high=bar.high, low=bar.low, symbol=symbol,
                            allow_targets=not filled_this_bar,
                        )
                        trade_records = []
                        for event in trade_events:
                            record = {
                                "symbol": symbol, "side": position.side.value,
                                "time": event_time, **event,
                                "realized_pnl": position.realized_pnl,
                            }
                            runtime.closed_trade_events.append(record)
                            trade_records.append(record)
                        cancelled_legs = []
                        if position.closed:
                            cancelled_legs = self._cancel_remaining_pending(
                                symbol, close_time,
                                "پوزیشن دمو بسته شد؛ لیمیت باقیمانده دیگر معتبر نیست",
                                persist=False, notify=False,
                            )
                        elif any(event.get("type") in {"TP1", "TP2", "TP3"} for event in trade_events):
                            cancelled_legs = self._cancel_remaining_pending(
                                symbol, close_time,
                                "هدف سود فعال شد؛ ورود عمیق‌تر برای همین معامله لغو شد",
                                persist=False, notify=False,
                            )
                        # Persist exit accounting and cancellation of the deeper
                        # entry leg in the same SQLite transaction before notifying.
                        if trade_events or cancelled_legs:
                            runtime.persist_demo_state()
                        for record in trade_records:
                            if self.on_trade_event:
                                self.on_trade_event(record)
                        if cancelled_legs:
                            self._notify_pending_cancellations(symbol, close_time, cancelled_legs)

                    # Persist the candle cursor even when no pending event fired,
                    # so a restart cannot replay an already-managed OHLC range.
                    runtime.persist_pending_limits()
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
                bars15 = _to_candles(self.market_data.fetch_ohlcv(market.symbol, "15m", 3520))
                engine = OrderBlockStrategy(market.symbol, strategy_config)
                signal, early_warning = _ordered_feed(engine, bars4, bars15)
                if (early_warning is not None and
                        self._last_early_warning_bar.get(early_warning.symbol) != early_warning.timestamp):
                    self._last_early_warning_bar[early_warning.symbol] = early_warning.timestamp
                    if self.on_early_warning:
                        self.on_early_warning(early_warning)
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
                trade_records = []
                for event in events:
                    record = {
                        "symbol": position.symbol,
                        "side": position.side.value,
                        "time": datetime.fromtimestamp((bar.timestamp + MS_15M) / 1000, timezone.utc).isoformat(),
                        **event,
                        "realized_pnl": position.realized_pnl,
                    }
                    runtime.closed_trade_events.append(record)
                    trade_records.append(record)
                close_time = bar.timestamp + MS_15M
                cancelled_legs = []
                if position.closed:
                    cancelled_legs = self._cancel_remaining_pending(
                        position.symbol, close_time,
                        "پوزیشن دمو بسته شد؛ لیمیت باقیمانده دیگر معتبر نیست",
                        persist=False, notify=False,
                    )
                elif any(event.get("type") in {"TP1", "TP2", "TP3"} for event in events):
                    cancelled_legs = self._cancel_remaining_pending(
                        position.symbol, close_time,
                        "هدف سود فعال شد؛ ورود عمیق‌تر برای همین معامله لغو شد",
                        persist=False, notify=False,
                    )
                if events or cancelled_legs:
                    runtime.persist_demo_state()
                for record in trade_records:
                    if self.on_trade_event:
                        self.on_trade_event(record)
                if cancelled_legs:
                    self._notify_pending_cancellations(position.symbol, close_time, cancelled_legs)
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
    """Open a risk-sized internal demo position with three partial targets; no exchange orders."""
    side = Side.LONG if signal.side == "BUY" else Side.SHORT
    try:
        entry_time = datetime.fromtimestamp((signal.timestamp + MS_15M) / 1000, timezone.utc).isoformat()
        position = runtime.account.open_position(
            signal.symbol, side, signal.entry, signal.stop, opened_at=entry_time,
            risk_percent=runtime.strategy_config.risk_percent, risk_reward=signal.risk_reward,
        )
        runtime.persist_demo_state()
        return position
    except ValueError as exc:
        log.info("Demo entry skipped for %s: %s", signal.symbol, exc)
        return None
