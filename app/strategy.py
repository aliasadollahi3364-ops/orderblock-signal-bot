"""Closed-candle implementation of the supplied 4H OB / 15m CHoCH strategy.

Feed candles chronologically. Call on_4h_close() only once for each completed
4-hour candle, then call on_15m_close() for each completed 15-minute candle.
This module is signal-only: it has no exchange order-placement capability.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("Candle high is inconsistent with OHLC.")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Candle low is inconsistent with OHLC.")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive.")


@dataclass
class StrategyConfig:
    pivot4: int = 3
    body_len: int = 10
    ob_search: int = 12
    max_stored_ob: int = 200
    pivot15: int = 3
    max_wait: int = 17
    atr_len: int = 7
    use_trend_filter: bool = False
    require_ob_touch: bool = True
    use_touch_freshness: bool = True
    max_bars_since_touch: int = 14
    use_choch_filter: bool = True
    use_choch_strength: bool = True
    min_choch_body_atr: float = 0.85
    use_retest_filter: bool = True
    retest_atr: float = 0.15
    use_confirm_candle: bool = True
    use_confirm_strength: bool = True
    min_confirm_body_atr: float = 0.20
    sl_buffer_atr: float = 0.15
    use_ob_boundary_sl: bool = True
    sma_length: int = 200
    use_sma200_filter: bool = False
    use_break_even: bool = True
    break_even_trigger_r: float = 1.0
    use_trailing_stop: bool = False
    trailing_atr_mult: float = 1.5  # Pine input is retained; source uses initial-risk distance.

    def __post_init__(self) -> None:
        positive_ints = (self.pivot4, self.body_len, self.ob_search,
                         self.max_stored_ob, self.pivot15, self.max_wait,
                         self.atr_len, self.max_bars_since_touch, self.sma_length)
        if any(v < 1 for v in positive_ints):
            raise ValueError("Integer strategy periods and limits must be positive.")
        if self.max_stored_ob < 1:
            raise ValueError("max_stored_ob must be positive.")


@dataclass
class OrderBlock:
    direction: int  # +1 bullish, -1 bearish
    top: float
    bottom: float
    origin_time: int
    active: bool = True
    used: bool = False
    touched: bool = False
    touch_bar: Optional[int] = None
    has_choch: bool = False
    choch_level: Optional[float] = None
    choch_bar: Optional[int] = None


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: str  # BUY or SELL
    timestamp: int
    entry: float
    stop: float
    atr: float
    ob_time: int
    ob_top: float
    ob_bottom: float
    trend4h: int
    sma200: Optional[float]

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.stop)


def _sma(values: list[float], length: int) -> Optional[float]:
    if len(values) < length:
        return None
    return sum(values[-length:]) / length


def _true_range(candle: Candle, previous_close: Optional[float]) -> float:
    if previous_close is None:
        return candle.high - candle.low
    return max(candle.high - candle.low,
               abs(candle.high - previous_close),
               abs(candle.low - previous_close))


class OrderBlockStrategy:
    def __init__(self, symbol: str, config: Optional[StrategyConfig] = None):
        self.symbol = symbol
        self.cfg = config or StrategyConfig()
        self.candles4: list[Candle] = []
        self.candles15: list[Candle] = []
        self.order_blocks: list[OrderBlock] = []
        self.trend4h = 0
        self.last_ph4: Optional[float] = None
        self.last_pl4: Optional[float] = None
        self.last_ph15: Optional[float] = None
        self.last_pl15: Optional[float] = None
        self._last_4h_timestamp: Optional[int] = None
        self._last_15m_timestamp: Optional[int] = None
        self._atr: Optional[float] = None
        self._tr_values: Deque[float] = deque(maxlen=self.cfg.atr_len)
        self._body_values: Deque[float] = deque(maxlen=self.cfg.body_len)
        self._sma_closes: Deque[float] = deque(maxlen=self.cfg.sma_length)

    @staticmethod
    def _pivot_high(bars: list[Candle], candidate: int, left: int, right: int) -> bool:
        if candidate - left < 0 or candidate + right >= len(bars):
            return False
        value = bars[candidate].high
        window = [b.high for b in bars[candidate-left:candidate+right+1]]
        return value == max(window) and window.count(value) == 1

    @staticmethod
    def _pivot_low(bars: list[Candle], candidate: int, left: int, right: int) -> bool:
        if candidate - left < 0 or candidate + right >= len(bars):
            return False
        value = bars[candidate].low
        window = [b.low for b in bars[candidate-left:candidate+right+1]]
        return value == min(window) and window.count(value) == 1

    def on_4h_close(self, candle: Candle) -> list[OrderBlock]:
        """Update confirmed 4H structure and return newly created OBs."""
        if self._last_4h_timestamp is not None and candle.timestamp <= self._last_4h_timestamp:
            raise ValueError("4H candles must be strictly chronological.")
        bars = self.candles4
        cfg = self.cfg
        previous_close = bars[-1].close if bars else None

        # Pine invalidates existing zones only with a confirmed HTF close.
        for ob in self.order_blocks:
            if ob.active and ((ob.direction == 1 and candle.close < ob.bottom) or
                              (ob.direction == -1 and candle.close > ob.top)):
                ob.active = False

        bars.append(candle)
        idx = len(bars) - 1
        candidate = idx - cfg.pivot4
        if candidate >= 0:
            if self._pivot_high(bars, candidate, cfg.pivot4, cfg.pivot4):
                self.last_ph4 = bars[candidate].high
            if self._pivot_low(bars, candidate, cfg.pivot4, cfg.pivot4):
                self.last_pl4 = bars[candidate].low

        body = abs(candle.close - candle.open)
        # Pine avgBody[1] is the SMA of the previous body_len completed bars.
        prior_average = (sum(self._body_values) / cfg.body_len
                         if len(self._body_values) == cfg.body_len else None)
        strong = prior_average is not None and body > prior_average
        bull_bos = (self.last_ph4 is not None and previous_close is not None and
                    candle.close > self.last_ph4 and previous_close <= self.last_ph4)
        bear_bos = (self.last_pl4 is not None and previous_close is not None and
                    candle.close < self.last_pl4 and previous_close >= self.last_pl4)
        created: list[OrderBlock] = []

        if bull_bos:
            self.trend4h = 1
            if strong:
                for prior in reversed(bars[max(0, idx-cfg.ob_search):idx]):
                    if prior.close < prior.open:
                        ob = OrderBlock(1, prior.high, prior.low, prior.timestamp)
                        created.append(ob)
                        break
        elif bear_bos:
            self.trend4h = -1
            if strong:
                for prior in reversed(bars[max(0, idx-cfg.ob_search):idx]):
                    if prior.close > prior.open:
                        ob = OrderBlock(-1, prior.high, prior.low, prior.timestamp)
                        created.append(ob)
                        break

        self.order_blocks.extend(created)
        if len(self.order_blocks) > cfg.max_stored_ob:
            self.order_blocks = self.order_blocks[-cfg.max_stored_ob:]
        self._body_values.append(body)
        self._last_4h_timestamp = candle.timestamp
        return created

    def on_15m_close(self, candle: Candle) -> Optional[Signal]:
        """Evaluate one completed 15m candle and return at most one signal."""
        if self._last_15m_timestamp is not None and candle.timestamp <= self._last_15m_timestamp:
            raise ValueError("15m candles must be strictly chronological.")
        bars = self.candles15
        cfg = self.cfg
        previous_close = bars[-1].close if bars else None
        bars.append(candle)
        idx = len(bars) - 1

        tr = _true_range(candle, previous_close)
        if self._atr is None:
            self._tr_values.append(tr)
            if len(self._tr_values) == cfg.atr_len:
                self._atr = sum(self._tr_values) / cfg.atr_len
        else:
            self._atr = ((self._atr * (cfg.atr_len - 1)) + tr) / cfg.atr_len

        candidate = idx - cfg.pivot15
        if candidate >= 0:
            if self._pivot_high(bars, candidate, cfg.pivot15, cfg.pivot15):
                self.last_ph15 = bars[candidate].high
            if self._pivot_low(bars, candidate, cfg.pivot15, cfg.pivot15):
                self.last_pl15 = bars[candidate].low

        self._sma_closes.append(candle.close)
        sma200 = _sma(list(self._sma_closes), cfg.sma_length)
        self._last_15m_timestamp = candle.timestamp
        if self._atr is None or self._atr <= 0:
            return None

        atr = self._atr
        tolerance = atr * cfg.retest_atr
        for ob in self.order_blocks:
            if not ob.active or ob.used:
                continue
            direction, top, bottom = ob.direction, ob.top, ob.bottom
            aligned = (not cfg.use_trend_filter or
                       (direction == 1 and self.trend4h == 1) or
                       (direction == -1 and self.trend4h == -1))
            if not aligned:
                continue

            touched_now = candle.low <= top and candle.high >= bottom
            if touched_now:
                ob.touched = True
                ob.touch_bar = idx
            touch_fresh = (ob.touched and
                           (not cfg.use_touch_freshness or
                            (ob.touch_bar is not None and idx - ob.touch_bar <= cfg.max_bars_since_touch)))
            can_use_ob = not cfg.require_ob_touch or touch_fresh

            bull_cross = (self.last_ph15 is not None and previous_close is not None and
                          candle.close > self.last_ph15 and previous_close <= self.last_ph15 and
                          candle.close > candle.open)
            bear_cross = (self.last_pl15 is not None and previous_close is not None and
                          candle.close < self.last_pl15 and previous_close >= self.last_pl15 and
                          candle.close < candle.open)
            bull_strong = (not cfg.use_choch_strength or
                           candle.close - candle.open >= atr * cfg.min_choch_body_atr)
            bear_strong = (not cfg.use_choch_strength or
                           candle.open - candle.close >= atr * cfg.min_choch_body_atr)

            if can_use_ob and cfg.use_choch_filter and not ob.has_choch and direction == 1 and bull_cross and bull_strong:
                ob.has_choch, ob.choch_level, ob.choch_bar = True, self.last_ph15, idx
            if can_use_ob and cfg.use_choch_filter and not ob.has_choch and direction == -1 and bear_cross and bear_strong:
                ob.has_choch, ob.choch_level, ob.choch_bar = True, self.last_pl15, idx

            if ob.has_choch and ob.choch_bar is not None and idx - ob.choch_bar > cfg.max_wait:
                ob.has_choch, ob.choch_level, ob.choch_bar = False, None, None

            after_choch = (not cfg.use_choch_filter or
                           (ob.has_choch and ob.choch_bar is not None and idx > ob.choch_bar))
            level = ob.choch_level if cfg.use_choch_filter else (
                self.last_ph15 if direction == 1 else self.last_pl15)
            retest_detected = (after_choch and level is not None and
                               candle.low <= level + tolerance and
                               candle.high >= level - tolerance)
            retest = not cfg.use_retest_filter or retest_detected

            bull_confirm_direction = (not cfg.use_confirm_candle or
                                      (candle.close > candle.open and
                                       (not cfg.use_retest_filter or level is None or candle.close > level)))
            bear_confirm_direction = (not cfg.use_confirm_candle or
                                      (candle.close < candle.open and
                                       (not cfg.use_retest_filter or level is None or candle.close < level)))
            bull_confirm_strength = (not cfg.use_confirm_strength or
                                     candle.close - candle.open >= atr * cfg.min_confirm_body_atr)
            bear_confirm_strength = (not cfg.use_confirm_strength or
                                     candle.open - candle.close >= atr * cfg.min_confirm_body_atr)

            if can_use_ob and retest and direction == 1 and bull_confirm_direction and bull_confirm_strength and self.last_pl15 is not None:
                base = min(self.last_pl15, bottom) if cfg.use_ob_boundary_sl else self.last_pl15
                stop = base - atr * cfg.sl_buffer_atr
                if stop < candle.close:
                    if not cfg.use_sma200_filter or (sma200 is not None and candle.close > sma200):
                        ob.used = True
                        return Signal(self.symbol, "BUY", candle.timestamp, candle.close, stop,
                                      atr, ob.origin_time, top, bottom, self.trend4h, sma200)

            if can_use_ob and retest and direction == -1 and bear_confirm_direction and bear_confirm_strength and self.last_ph15 is not None:
                base = max(self.last_ph15, top) if cfg.use_ob_boundary_sl else self.last_ph15
                stop = base + atr * cfg.sl_buffer_atr
                if stop > candle.close:
                    if not cfg.use_sma200_filter or (sma200 is not None and candle.close < sma200):
                        ob.used = True
                        return Signal(self.symbol, "SELL", candle.timestamp, candle.close, stop,
                                      atr, ob.origin_time, top, bottom, self.trend4h, sma200)

        return None
