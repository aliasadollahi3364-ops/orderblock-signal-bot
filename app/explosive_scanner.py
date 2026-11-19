"""Independent, alert-only strict scanner for potentially explosive moves.

It never opens demo positions and never submits exchange orders. It uses only
closed OHLC candles plus public WEEX trades/order-book snapshots.
"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from statistics import median
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.market_radar import RadarAlert, evaluate_market_radar
from app.strategy import Candle, OrderBlockStrategy, StrategyConfig

log = logging.getLogger(__name__)
DURATION_MS = {"1d": 24 * 60 * 60 * 1000, "4h": 4 * 60 * 60 * 1000, "1h": 60 * 60 * 1000,
               "15m": 15 * 60 * 1000, "5m": 5 * 60 * 1000}


@dataclass(frozen=True)
class ExplosiveAlert:
    symbol: str
    side: str
    stage: str
    score: int
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    timestamp: int
    reasons: tuple[str, ...]


def _closed_candles(rows: list[list[float]], timeframe: str, now_ms: int) -> list[Candle]:
    result: list[Candle] = []
    duration = DURATION_MS[timeframe]
    for row in rows:
        if len(row) < 6:
            continue
        try:
            ts = int(row[0])
            values = list(map(float, row[1:6]))
            o, h, low, close, volume = values
            if not all(math.isfinite(value) for value in values):
                continue
            if ts + duration > now_ms or min(o, h, low, close) <= 0 or volume < 0:
                continue
            if h < max(o, close, low) or low > min(o, close, h):
                continue
            result.append(Candle(ts, o, h, low, close, volume))
        except (TypeError, ValueError, OverflowError):
            continue
    return sorted({bar.timestamp: bar for bar in result}.values(), key=lambda b: b.timestamp)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _atr(bars: list[Candle], length: int = 14) -> float:
    if len(bars) < length + 1:
        return 0.0
    tr = []
    for i in range(1, len(bars)):
        prev = bars[i - 1].close
        bar = bars[i]
        tr.append(max(bar.high - bar.low, abs(bar.high - prev), abs(bar.low - prev)))
    return _mean(tr[-length:])


def _ema(values: list[float], length: int) -> float | None:
    if len(values) < length:
        return None
    alpha = 2.0 / (length + 1)
    value = _mean(values[:length])
    for item in values[length:]:
        value = alpha * item + (1 - alpha) * value
    return value


def _trade_buy_ratio(trades: list[dict[str, Any]]) -> float | None:
    buy = sell = 0.0
    for trade in trades:
        try:
            volume = float(trade.get("volume") or 0)
            side = str(trade.get("type") or "").lower()
            if volume <= 0:
                continue
            if side == "buy":
                buy += volume
            elif side == "sell":
                sell += volume
        except (TypeError, ValueError):
            continue
    total = buy + sell
    return buy / total if total > 0 else None


def _book_ratio(book: dict[str, Any]) -> float | None:
    try:
        bids = sum(float(level[1]) for level in book.get("bids", [])[:10])
        asks = sum(float(level[1]) for level in book.get("asks", [])[:10])
        return bids / asks if asks > 0 else None
    except (TypeError, ValueError, IndexError):
        return None

def _trade_poc(trades: list[dict[str, Any]]) -> float | None:
    """Approximate point of control from the latest public trade sample."""
    parsed = []
    for trade in trades:
        try:
            price = float(trade.get("price") or 0)
            volume = float(trade.get("volume") or 0)
            if price > 0 and volume > 0 and math.isfinite(price) and math.isfinite(volume):
                parsed.append((price, volume))
        except (TypeError, ValueError):
            continue
    if not parsed:
        return None
    step = median([price for price, _ in parsed]) * 0.002
    if step <= 0:
        return None
    buckets: dict[int, float] = defaultdict(float)
    for price, volume in parsed:
        buckets[round(price / step)] += volume
    return max(buckets.items(), key=lambda item: item[1])[0] * step


def _ob_for_side(bars4: list[Candle], side: str, price: float) -> tuple[bool, float | None, float | None]:
    engine = OrderBlockStrategy("EXPLOSIVE", StrategyConfig())
    for bar in bars4:
        engine.on_4h_close(bar)
    direction = 1 if side == "BUY" else -1
    candidates = [ob for ob in engine.order_blocks if ob.active and ob.direction == direction]
    if not candidates:
        return False, None, None
    def distance(ob: Any) -> float:
        if ob.bottom <= price <= ob.top:
            return 0.0
        return min(abs(price - ob.top), abs(price - ob.bottom)) / max(price, 1e-12)
    chosen = min(candidates, key=distance)
    return distance(chosen) <= 0.04, chosen.top, chosen.bottom


def _sweep(bars: list[Candle], side: str) -> bool:
    if len(bars) < 22:
        return False
    last = bars[-1]
    prior = bars[-21:-1]
    if side == "BUY":
        level = min(bar.low for bar in prior)
        return last.low < level and last.close > level
    level = max(bar.high for bar in prior)
    return last.high > level and last.close < level


def _choch(bars: list[Candle], side: str) -> bool:
    if len(bars) < 8:
        return False
    last, prior = bars[-1], bars[-7:-1]
    return last.close > max(b.high for b in prior) if side == "BUY" else last.close < min(b.low for b in prior)


def _break_retest(bars: list[Candle], side: str) -> bool:
    if len(bars) < 24:
        return False
    last = bars[-1]
    # A breakout must have occurred on a prior closed candle, followed by a
    # later candle that revisits the broken level and closes back in direction.
    for i in range(max(20, len(bars) - 7), len(bars) - 1):
        history = bars[max(0, i - 20):i]
        if len(history) < 15:
            continue
        level = max(b.high for b in history) if side == "BUY" else min(b.low for b in history)
        broken = bars[i].close > level if side == "BUY" else bars[i].close < level
        if not broken:
            continue
        tolerance = max(level * 0.002, _atr(bars[-20:]) * 0.15)
        touched = last.low <= level + tolerance if side == "BUY" else last.high >= level - tolerance
        held = last.close > level if side == "BUY" else last.close < level
        if touched and held:
            return True
    return False


def _compression(bars1h: list[Candle]) -> bool:
    if len(bars1h) < 30:
        return False
    recent = _mean([b.high - b.low for b in bars1h[-8:]])
    baseline = _mean([b.high - b.low for b in bars1h[-28:-8]])
    return baseline > 0 and recent <= baseline * 0.80


def _volume_spike(bars15: list[Candle]) -> bool:
    if len(bars15) < 22:
        return False
    baseline = _mean([b.volume for b in bars15[-21:-1]])
    return baseline > 0 and bars15[-1].volume >= baseline * 1.8


def _trend_strength(bars1h: list[Candle], side: str) -> bool:
    closes = [b.close for b in bars1h]
    fast, slow = _ema(closes, 20), _ema(closes, 50)
    if fast is None or slow is None:
        return False
    return closes[-1] > fast > slow if side == "BUY" else closes[-1] < fast < slow


def _five_minute_timing(bars5: list[Candle], side: str) -> bool:
    if len(bars5) < 5:
        return False
    last, prior = bars5[-1], bars5[-4:-1]
    if side == "BUY":
        return last.close > last.open and last.close > max(b.high for b in prior)
    return last.close < last.open and last.close < min(b.low for b in prior)


class ExplosiveMoveScanner:
    """Strict multi-timeframe alert engine; early warnings upgrade to confirmed."""

    def __init__(self, market_data: Any, on_alert: Callable[[ExplosiveAlert], None] | None = None,
                 on_radar_alert: Callable[[RadarAlert], None] | None = None):
        self.market_data = market_data
        self.on_alert = on_alert
        self.on_radar_alert = on_radar_alert
        self._state: dict[tuple[str, str], tuple[str, int]] = {}
        self._radar_state: dict[tuple[str, str], tuple[str, int]] = {}
        self._daily_cache: dict[str, tuple[int, list[Candle]]] = {}

    def _evaluate_side(self, symbol: str, side: str, bars4: list[Candle],
                       bars1h: list[Candle], bars15: list[Candle], bars5: list[Candle],
                       trades: list[dict[str, Any]], book: dict[str, Any]) -> ExplosiveAlert | None:
        if min(len(bars4), len(bars1h), len(bars15), len(bars5)) < 30:
            return None
        last = bars15[-1]
        entry = bars5[-1].close
        compressed = _compression(bars1h)
        volume_ok = _volume_spike(bars15)
        ratio, book_ratio = _trade_buy_ratio(trades), _book_ratio(book)
        poc = _trade_poc(trades)
        trade_flow_ok = ratio is not None and (ratio >= 0.62 if side == "BUY" else ratio <= 0.38)
        book_flow_ok = book_ratio is not None and (book_ratio >= 1.20 if side == "BUY" else book_ratio <= 0.83)
        poc_ok = poc is not None and (last.close >= poc if side == "BUY" else last.close <= poc)
        flow_score = (8 if trade_flow_ok else 0) + (4 if book_flow_ok else 0) + (3 if poc_ok else 0)
        ob_ok, ob_top, ob_bottom = _ob_for_side(bars4, side, last.close)
        sweep_ok = _sweep(bars15, side)
        choch_ok = _choch(bars15, side)
        retest_ok = _break_retest(bars15, side)
        trend_ok = _trend_strength(bars1h, side)
        score = (
            (15 if compressed else 0) +
            (15 if volume_ok else 0) +
            flow_score +
            (15 if ob_ok else 0) +
            (10 if sweep_ok else 0) +
            (10 if choch_ok else 0) +
            (15 if retest_ok else 0) +
            (5 if trend_ok else 0)
        )
        # Early warnings need a strong setup near a local breakout boundary.
        prior_high = max(b.high for b in bars15[-21:-1])
        prior_low = min(b.low for b in bars15[-21:-1])
        near_break = (last.close >= prior_high * 0.995) if side == "BUY" else (last.close <= prior_low * 1.005)
        early = score >= 75 and compressed and volume_ok and trade_flow_ok and ob_ok and near_break
        confirmed = (
            score >= 85 and volume_ok and trade_flow_ok and book_flow_ok and poc_ok and ob_ok
            and choch_ok and retest_ok and _five_minute_timing(bars5, side)
        )
        if not early and not confirmed:
            return None
        stage = "CONFIRMED" if confirmed else "EARLY"
        atr = _atr(bars15)
        if atr <= 0:
            return None
        if side == "BUY":
            structural_stop = min([last.low] + ([ob_bottom] if ob_bottom else []))
            stop = structural_stop - atr * 0.20
            risk = entry - stop
            if risk <= 0:
                return None
            targets = (entry + risk, entry + risk * 2, entry + risk * 3)
        else:
            structural_stop = max([last.high] + ([ob_top] if ob_top else []))
            stop = structural_stop + atr * 0.20
            risk = stop - entry
            if risk <= 0:
                return None
            targets = (entry - risk, entry - risk * 2, entry - risk * 3)
        reasons = []
        if compressed: reasons.append("تراکم/فشردگی در ۱ساعته")
        if volume_ok: reasons.append("افزایش حجم غیرعادی ۱۵دقیقه")
        if trade_flow_ok: reasons.append(f"غلبه معاملات {'خرید' if side == 'BUY' else 'فروش'} ({ratio:.0%})")
        if book_flow_ok: reasons.append("عدم‌تعادل همسو در اردربوک")
        if poc_ok: reasons.append("قرارگیری قیمت در سمت همسو با POC حجم معاملات اخیر")
        if ob_ok: reasons.append("اردربلاک معتبر هم‌جهت ۴ساعته")
        if sweep_ok: reasons.append("جمع‌آوری نقدینگی")
        if choch_ok: reasons.append("شکست ساختار/CHoCH")
        if retest_ok: reasons.append("شکست و پولبک موفق")
        if trend_ok: reasons.append("قدرت روند ۱ساعته")
        return ExplosiveAlert(
            symbol=symbol, side=side, stage=stage, score=score, entry=entry, stop=stop,
            tp1=targets[0], tp2=targets[1], tp3=targets[2],
            timestamp=bars5[-1].timestamp, reasons=tuple(reasons),
        )

    def scan_once(self, symbols: list[str]) -> list[ExplosiveAlert]:
        alerts: list[ExplosiveAlert] = []
        now_ms = int(time.time() * 1000)
        for symbol in list(dict.fromkeys(symbols)):
            try:
                bars = {
                    tf: _closed_candles(self.market_data.fetch_ohlcv(symbol, tf, 220 if tf == "4h" else 320 if tf == "15m" else 120), tf, now_ms)
                    for tf in ("4h", "1h", "15m", "5m")
                }
                if any(len(bars[tf]) < 30 for tf in bars):
                    continue
                if self.on_radar_alert:
                    day_bucket = now_ms // DURATION_MS["1d"]
                    cached_daily = self._daily_cache.get(symbol)
                    if cached_daily is None or cached_daily[0] != day_bucket:
                        daily = _closed_candles(self.market_data.fetch_ohlcv(symbol, "1d", 400), "1d", now_ms)
                        self._daily_cache[symbol] = (day_bucket, daily)
                    else:
                        daily = cached_daily[1]
                    radar_price = bars["15m"][-1].close
                    bullish_ob_near, _, _ = _ob_for_side(bars["4h"], "BUY", radar_price)
                    bearish_ob_near, _, _ = _ob_for_side(bars["4h"], "SELL", radar_price)
                    for radar_alert in evaluate_market_radar(
                        symbol, daily, bars["4h"], bars["1h"], bars["15m"],
                        bullish_ob_near=bullish_ob_near,
                        bearish_ob_near=bearish_ob_near,
                    ):
                        key = (symbol, radar_alert.category)
                        previous = self._radar_state.get(key)
                        rank = {"WATCH": 1, "PRE_SIGNAL": 2, "CONFIRMED": 3}
                        if previous and (
                            rank[previous[0]] >= rank[radar_alert.stage]
                            and now_ms - previous[1] < 24 * 60 * 60 * 1000
                        ):
                            continue
                        self._radar_state[key] = (radar_alert.stage, now_ms)
                        self.on_radar_alert(radar_alert)
                trades = self.market_data.fetch_trades(symbol)
                book = self.market_data.fetch_orderbook(symbol)
                for side in ("BUY", "SELL"):
                    alert = self._evaluate_side(
                        symbol, side, bars["4h"], bars["1h"], bars["15m"], bars["5m"], trades, book
                    )
                    if alert is None:
                        continue
                    key = (symbol, side)
                    previous = self._state.get(key)
                    if previous:
                        if previous[0] == "CONFIRMED":
                            continue
                        if previous[0] == "EARLY" and alert.stage == "EARLY" and (
                            alert.timestamp - previous[1] < 4 * 60 * 60 * 1000
                        ):
                            continue
                    self._state[key] = (alert.stage, alert.timestamp)
                    alerts.append(alert)
                    if self.on_alert:
                        self.on_alert(alert)
            except Exception:
                log.exception("Explosive-move scan failed for %s", symbol)
        # Expire old alert state so a genuinely new setup can alert again later.
        cutoff = now_ms - 24 * 60 * 60 * 1000
        self._state = {
            key: value for key, value in self._state.items()
            if value[1] >= cutoff
        }
        self._radar_state = {
            key: value for key, value in self._radar_state.items()
            if value[1] >= cutoff
        }
        return alerts
