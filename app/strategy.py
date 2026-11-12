"""Closed-candle implementation of the supplied 4H OB + 15m OB/Trendline strategy."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
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
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("Candle high/low is inconsistent with OHLC.")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive.")


@dataclass
class StrategyConfig:
    pivot4: int = 3
    body_len: int = 10
    ob_search: int = 12
    max_stored_ob: int = 200
    ob_atr_len: int = 14
    min_bos_body_atr: float = 0.65
    min_bos_body_avg: float = 1.20
    min_break_atr: float = 0.10
    max_ob_candle_range_atr: float = 2.50
    pivot15: int = 3
    max_wait: int = 17
    atr_len: int = 7
    enforce_15m: bool = True
    use_trend_filter: bool = True
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
    enable_trendline_signals: bool = True
    use_sma200_trendline_filter: bool = True
    require_trendline_pullback: bool = True
    trendline_retest_atr: float = 0.15
    trendline_pullback_max_wait: int = 12
    risk_percent: float = 1.0
    risk_reward: float = 2.5
    sl_buffer_atr: float = 2.0
    use_ob_boundary_sl: bool = True
    use_trailing_stop: bool = False
    trailing_atr_mult: float = 1.5
    sma_length: int = 200
    use_sma200_filter: bool = True
    min_tick: float = 0.00000001

    def __post_init__(self) -> None:
        for name in ("pivot4", "body_len", "ob_search", "max_stored_ob", "ob_atr_len",
                     "pivot15", "max_wait", "atr_len", "max_bars_since_touch",
                     "trendline_pullback_max_wait", "sma_length"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.min_tick <= 0:
            raise ValueError("min_tick must be positive")


@dataclass
class OrderBlock:
    direction: int
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
    side: str
    timestamp: int
    entry: float
    stop: float
    atr: float
    ob_time: int
    ob_top: float
    ob_bottom: float
    trend4h: int
    sma200: Optional[float]
    method: str = "ORDER_BLOCK"
    risk_reward: float = 2.5

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def target(self) -> float:
        return self.entry + self.risk_distance * self.risk_reward * (1 if self.side == "BUY" else -1)


def _sma(values: list[float], length: int) -> Optional[float]:
    return sum(values[-length:]) / length if len(values) >= length else None


def _tr(c: Candle, previous: Optional[float]) -> float:
    return c.high - c.low if previous is None else max(c.high-c.low, abs(c.high-previous), abs(c.low-previous))


def _atr(values: list[float], length: int) -> Optional[float]:
    if len(values) < length:
        return None
    result = sum(values[:length]) / length
    for value in values[length:]:
        result = (result * (length - 1) + value) / length
    return result


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
        self.prev_ph15: Optional[float] = None
        self.prev_pl15: Optional[float] = None
        self.last_ph15_bar: Optional[int] = None
        self.last_pl15_bar: Optional[int] = None
        self.prev_ph15_bar: Optional[int] = None
        self.prev_pl15_bar: Optional[int] = None
        self._last_4h_timestamp: Optional[int] = None
        self._last_15m_timestamp: Optional[int] = None
        self._tr4: list[float] = []
        self._tr15: list[float] = []
        self._body4: Deque[float] = deque(maxlen=self.cfg.body_len)
        self._sma4: Deque[float] = deque(maxlen=self.cfg.sma_length)
        self.sma200_4h: Optional[float] = None
        self._long_pending = False
        self._short_pending = False
        self._long_level: Optional[float] = None
        self._short_level: Optional[float] = None
        self._long_break_bar: Optional[int] = None
        self._short_break_bar: Optional[int] = None
        self._long_sl: Optional[float] = None
        self._short_sl: Optional[float] = None

    @staticmethod
    def _pivot_high(bars: list[Candle], candidate: int, left: int, right: int) -> bool:
        if candidate-left < 0 or candidate+right >= len(bars): return False
        values = [b.high for b in bars[candidate-left:candidate+right+1]]
        return bars[candidate].high == max(values) and values.count(bars[candidate].high) == 1

    @staticmethod
    def _pivot_low(bars: list[Candle], candidate: int, left: int, right: int) -> bool:
        if candidate-left < 0 or candidate+right >= len(bars): return False
        values = [b.low for b in bars[candidate-left:candidate+right+1]]
        return bars[candidate].low == min(values) and values.count(bars[candidate].low) == 1

    def on_4h_close(self, candle: Candle) -> list[OrderBlock]:
        if self._last_4h_timestamp is not None and candle.timestamp <= self._last_4h_timestamp:
            raise ValueError("4H candles must be strictly chronological.")
        bars = self.candles4
        previous_close = bars[-1].close if bars else None
        for ob in self.order_blocks:
            if ob.active and ((ob.direction == 1 and candle.close < ob.bottom) or
                              (ob.direction == -1 and candle.close > ob.top)):
                ob.active = False
        self._tr4.append(_tr(candle, previous_close))
        atr4 = _atr(self._tr4, self.cfg.ob_atr_len)
        bars.append(candle)
        idx = len(bars)-1
        candidate = idx-self.cfg.pivot4
        if candidate >= 0:
            if self._pivot_high(bars, candidate, self.cfg.pivot4, self.cfg.pivot4):
                self.last_ph4 = bars[candidate].high
            if self._pivot_low(bars, candidate, self.cfg.pivot4, self.cfg.pivot4):
                self.last_pl4 = bars[candidate].low
        body = abs(candle.close-candle.open)
        avg_body_prior = sum(self._body4)/len(self._body4) if len(self._body4) == self.cfg.body_len else None
        strong_body = (avg_body_prior is not None and atr4 is not None and
                       body >= avg_body_prior*self.cfg.min_bos_body_avg and
                       body >= atr4*self.cfg.min_bos_body_atr)
        bull_bos = self.last_ph4 is not None and previous_close is not None and candle.close > self.last_ph4 and previous_close <= self.last_ph4
        bear_bos = self.last_pl4 is not None and previous_close is not None and candle.close < self.last_pl4 and previous_close >= self.last_pl4
        created: list[OrderBlock] = []
        if bull_bos: self.trend4h = 1
        if bear_bos: self.trend4h = -1
        if strong_body and atr4 is not None and bull_bos and candle.close > candle.open and candle.close-self.last_ph4 >= atr4*self.cfg.min_break_atr:
            found = next((j for j in range(idx-1, max(-1, idx-self.cfg.ob_search-1), -1) if bars[j].close < bars[j].open), None)
            if found is not None:
                source = bars[found]
                source_atr = _atr(self._tr4[:found+1], self.cfg.ob_atr_len)
                if source_atr is not None and 0 < source.high-source.low <= source_atr*self.cfg.max_ob_candle_range_atr:
                    created.append(OrderBlock(1, source.high, source.low, source.timestamp))
        if strong_body and atr4 is not None and bear_bos and candle.close < candle.open and self.last_pl4-candle.close >= atr4*self.cfg.min_break_atr:
            found = next((j for j in range(idx-1, max(-1, idx-self.cfg.ob_search-1), -1) if bars[j].close > bars[j].open), None)
            if found is not None:
                source = bars[found]
                source_atr = _atr(self._tr4[:found+1], self.cfg.ob_atr_len)
                if source_atr is not None and 0 < source.high-source.low <= source_atr*self.cfg.max_ob_candle_range_atr:
                    created.append(OrderBlock(-1, source.high, source.low, source.timestamp))
        self.order_blocks.extend(created)
        while len(self.order_blocks) > self.cfg.max_stored_ob:
            self.order_blocks.pop(0)
        self._body4.append(body)
        self._sma4.append(candle.close)
        self.sma200_4h = _sma(list(self._sma4), self.cfg.sma_length)
        self._last_4h_timestamp = candle.timestamp
        return created

    @staticmethod
    def _line_value(p1: float, b1: int, p2: float, b2: int, at: int) -> float:
        return p1 + (p2-p1) * (at-b1) / (b2-b1)

    def _nearest_touched_ob_direction(self, candle: Candle) -> int:
        nearest_direction, nearest_distance = 0, None
        for ob in self.order_blocks:
            if not ob.active: continue
            if candle.low <= ob.top and candle.high >= ob.bottom:
                distance = candle.close-ob.top if candle.close > ob.top else ob.bottom-candle.close if candle.close < ob.bottom else 0.0
                if nearest_distance is None or distance < nearest_distance:
                    nearest_distance, nearest_direction = distance, ob.direction
        return nearest_direction

    def on_15m_close(self, candle: Candle) -> Optional[Signal]:
        if self._last_15m_timestamp is not None and candle.timestamp <= self._last_15m_timestamp:
            raise ValueError("15m candles must be strictly chronological.")
        bars = self.candles15
        previous_close = bars[-1].close if bars else None
        bars.append(candle)
        idx = len(bars)-1
        self._tr15.append(_tr(candle, previous_close))
        atr = _atr(self._tr15, self.cfg.atr_len)
        candidate = idx-self.cfg.pivot15
        if candidate >= 0:
            if self._pivot_high(bars, candidate, self.cfg.pivot15, self.cfg.pivot15):
                self.prev_ph15, self.prev_ph15_bar = self.last_ph15, self.last_ph15_bar
                self.last_ph15, self.last_ph15_bar = bars[candidate].high, candidate
            if self._pivot_low(bars, candidate, self.cfg.pivot15, self.cfg.pivot15):
                self.prev_pl15, self.prev_pl15_bar = self.last_pl15, self.last_pl15_bar
                self.last_pl15, self.last_pl15_bar = bars[candidate].low, candidate
        self._last_15m_timestamp = candle.timestamp
        if atr is None or atr <= 0: return None
        cfg, sma = self.cfg, self.sma200_4h

        resistance_now = resistance_prev = support_now = support_prev = None
        descending = self.prev_ph15 is not None and self.last_ph15 is not None and self.last_ph15 <= self.prev_ph15
        ascending = self.prev_pl15 is not None and self.last_pl15 is not None and self.last_pl15 >= self.prev_pl15
        if descending and self.prev_ph15_bar is not None and self.last_ph15_bar is not None and self.last_ph15_bar != self.prev_ph15_bar:
            resistance_now = self._line_value(self.prev_ph15, self.prev_ph15_bar, self.last_ph15, self.last_ph15_bar, idx)
            resistance_prev = self._line_value(self.prev_ph15, self.prev_ph15_bar, self.last_ph15, self.last_ph15_bar, idx-1)
        if ascending and self.prev_pl15_bar is not None and self.last_pl15_bar is not None and self.last_pl15_bar != self.prev_pl15_bar:
            support_now = self._line_value(self.prev_pl15, self.prev_pl15_bar, self.last_pl15, self.last_pl15_bar, idx)
            support_prev = self._line_value(self.prev_pl15, self.prev_pl15_bar, self.last_pl15, self.last_pl15_bar, idx-1)
        break_up = cfg.enable_trendline_signals and resistance_now is not None and previous_close is not None and candle.close > resistance_now and previous_close <= resistance_prev
        break_down = cfg.enable_trendline_signals and support_now is not None and previous_close is not None and candle.close < support_now and previous_close >= support_prev
        if break_up:
            self._long_pending, self._long_level, self._long_break_bar = True, resistance_now, idx
            self._long_sl = self.last_pl15-atr*cfg.sl_buffer_atr if self.last_pl15 is not None else None
            self._short_pending, self._short_level, self._short_break_bar, self._short_sl = False, None, None, None
        if break_down:
            self._short_pending, self._short_level, self._short_break_bar = True, support_now, idx
            self._short_sl = self.last_ph15+atr*cfg.sl_buffer_atr if self.last_ph15 is not None else None
            self._long_pending, self._long_level, self._long_break_bar, self._long_sl = False, None, None, None
        tol = atr*cfg.trendline_retest_atr
        if self._long_pending and self._long_break_bar is not None and (idx-self._long_break_bar > cfg.trendline_pullback_max_wait or candle.close < self._long_level-tol):
            self._long_pending, self._long_level, self._long_break_bar, self._long_sl = False, None, None, None
        if self._short_pending and self._short_break_bar is not None and (idx-self._short_break_bar > cfg.trendline_pullback_max_wait or candle.close > self._short_level+tol):
            self._short_pending, self._short_level, self._short_break_bar, self._short_sl = False, None, None, None
        long_retest = self._long_pending and self._long_break_bar is not None and idx > self._long_break_bar and candle.low <= self._long_level+tol and candle.high >= self._long_level-tol
        short_retest = self._short_pending and self._short_break_bar is not None and idx > self._short_break_bar and candle.high >= self._short_level-tol and candle.low <= self._short_level+tol
        long_confirm = long_retest and candle.close > candle.open and candle.close > self._long_level
        short_confirm = short_retest and candle.close < candle.open and candle.close < self._short_level
        nearest_dir = self._nearest_touched_ob_direction(candle)
        tl_long = (cfg.enable_trendline_signals and nearest_dir == 1 and
                   (long_confirm if cfg.require_trendline_pullback else break_up) and
                   (not cfg.use_sma200_trendline_filter or (sma is not None and candle.close > sma)) and
                   self._long_sl is not None and self._long_sl < candle.close)
        tl_short = (cfg.enable_trendline_signals and nearest_dir == -1 and
                    (short_confirm if cfg.require_trendline_pullback else break_down) and
                    (not cfg.use_sma200_trendline_filter or (sma is not None and candle.close < sma)) and
                    self._short_sl is not None and self._short_sl > candle.close)

        tolerance = atr*cfg.retest_atr
        for ob in self.order_blocks:
            if not ob.active or ob.used: continue
            direction, top, bottom = ob.direction, ob.top, ob.bottom
            aligned = (not cfg.use_trend_filter or (direction == 1 and self.trend4h == 1) or (direction == -1 and self.trend4h == -1))
            if not aligned: continue
            if candle.low <= top and candle.high >= bottom: ob.touched, ob.touch_bar = True, idx
            fresh = ob.touched and (not cfg.use_touch_freshness or (ob.touch_bar is not None and idx-ob.touch_bar <= cfg.max_bars_since_touch))
            can_use = not cfg.require_ob_touch or fresh
            bull_cross = self.last_ph15 is not None and previous_close is not None and candle.close > self.last_ph15 and previous_close <= self.last_ph15 and candle.close > candle.open
            bear_cross = self.last_pl15 is not None and previous_close is not None and candle.close < self.last_pl15 and previous_close >= self.last_pl15 and candle.close < candle.open
            bull_strong = not cfg.use_choch_strength or candle.close-candle.open >= atr*cfg.min_choch_body_atr
            bear_strong = not cfg.use_choch_strength or candle.open-candle.close >= atr*cfg.min_choch_body_atr
            if can_use and cfg.use_choch_filter and not ob.has_choch and direction == 1 and bull_cross and bull_strong:
                ob.has_choch, ob.choch_level, ob.choch_bar = True, self.last_ph15, idx
            if can_use and cfg.use_choch_filter and not ob.has_choch and direction == -1 and bear_cross and bear_strong:
                ob.has_choch, ob.choch_level, ob.choch_bar = True, self.last_pl15, idx
            if ob.has_choch and ob.choch_bar is not None and idx-ob.choch_bar > cfg.max_wait:
                ob.has_choch, ob.choch_level, ob.choch_bar = False, None, None
            after = not cfg.use_choch_filter or (ob.has_choch and ob.choch_bar is not None and idx > ob.choch_bar)
            level = ob.choch_level if cfg.use_choch_filter else (self.last_ph15 if direction == 1 else self.last_pl15)
            detected = after and level is not None and candle.low <= level+tolerance and candle.high >= level-tolerance
            retest = not cfg.use_retest_filter or detected
            bull_dir = not cfg.use_confirm_candle or (candle.close > candle.open and (not cfg.use_retest_filter or level is None or candle.close > level))
            bear_dir = not cfg.use_confirm_candle or (candle.close < candle.open and (not cfg.use_retest_filter or level is None or candle.close < level))
            bull_power = not cfg.use_confirm_strength or candle.close-candle.open >= atr*cfg.min_confirm_body_atr
            bear_power = not cfg.use_confirm_strength or candle.open-candle.close >= atr*cfg.min_confirm_body_atr
            if can_use and retest and direction == 1 and bull_dir and bull_power and self.last_pl15 is not None:
                base = min(self.last_pl15, bottom) if cfg.use_ob_boundary_sl else self.last_pl15
                stop = base-atr*cfg.sl_buffer_atr
                if candle.close-stop >= cfg.min_tick:
                    if cfg.use_sma200_filter and (sma is None or candle.close <= sma): continue
                    ob.used = True
                    return Signal(self.symbol, "BUY", candle.timestamp, candle.close, stop, atr, ob.origin_time, top, bottom, self.trend4h, sma, "ORDER_BLOCK", cfg.risk_reward)
            if can_use and retest and direction == -1 and bear_dir and bear_power and self.last_ph15 is not None:
                base = max(self.last_ph15, top) if cfg.use_ob_boundary_sl else self.last_ph15
                stop = base+atr*cfg.sl_buffer_atr
                if stop-candle.close >= cfg.min_tick:
                    if cfg.use_sma200_filter and (sma is None or candle.close >= sma): continue
                    ob.used = True
                    return Signal(self.symbol, "SELL", candle.timestamp, candle.close, stop, atr, ob.origin_time, top, bottom, self.trend4h, sma, "ORDER_BLOCK", cfg.risk_reward)

        if tl_long:
            signal = Signal(self.symbol, "BUY", candle.timestamp, candle.close, self._long_sl, atr, 0, 0.0, 0.0, self.trend4h, sma, "TRENDLINE", cfg.risk_reward)
            self._long_pending, self._long_level, self._long_break_bar, self._long_sl = False, None, None, None
            return signal
        if tl_short:
            signal = Signal(self.symbol, "SELL", candle.timestamp, candle.close, self._short_sl, atr, 0, 0.0, 0.0, self.trend4h, sma, "TRENDLINE", cfg.risk_reward)
            self._short_pending, self._short_level, self._short_break_bar, self._short_sl = False, None, None, None
            return signal
        return None
