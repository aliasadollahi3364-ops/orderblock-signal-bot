"""Independent five-category market radar.

This module is alert-only. It does not import or modify the trading strategy,
open demo positions, or submit exchange orders. ATH/ATL references are limited
to the daily candle history supplied by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.strategy import Candle


@dataclass(frozen=True)
class RadarAlert:
    symbol: str
    category: str
    side: str
    stage: str
    score: int
    timestamp: int
    reference_price: float
    reasons: tuple[str, ...]


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _stage(score: int) -> str | None:
    if score >= 80:
        return "CONFIRMED"
    if score >= 65:
        return "PRE_SIGNAL"
    if score >= 50:
        return "WATCH"
    return None


def _volume_ratio(bars: list[Candle], lookback: int = 20) -> float:
    if len(bars) < lookback + 1:
        return 0.0
    baseline = _mean([bar.volume for bar in bars[-lookback-1:-1]])
    return bars[-1].volume / baseline if baseline > 0 else 0.0


def _range_compression(bars: list[Candle]) -> bool:
    if len(bars) < 30:
        return False
    recent = _mean([bar.high - bar.low for bar in bars[-8:]])
    baseline = _mean([bar.high - bar.low for bar in bars[-28:-8]])
    return baseline > 0 and recent <= baseline * 0.80


def _bullish_structure(bars: list[Candle]) -> bool:
    if len(bars) < 8:
        return False
    return bars[-1].close > max(bar.high for bar in bars[-7:-1])


def _bearish_structure(bars: list[Candle]) -> bool:
    if len(bars) < 8:
        return False
    return bars[-1].close < min(bar.low for bar in bars[-7:-1])


def _sweep_low(bars: list[Candle]) -> bool:
    if len(bars) < 22:
        return False
    level = min(bar.low for bar in bars[-21:-1])
    return bars[-1].low < level and bars[-1].close > level


def _sweep_high(bars: list[Candle]) -> bool:
    if len(bars) < 22:
        return False
    level = max(bar.high for bar in bars[-21:-1])
    return bars[-1].high > level and bars[-1].close < level


def _alert(symbol: str, category: str, side: str, score: int,
           bar: Candle, reasons: list[str]) -> RadarAlert | None:
    stage = _stage(score)
    if stage is None:
        return None
    return RadarAlert(symbol, category, side, stage, min(100, score),
                      bar.timestamp, bar.close, tuple(reasons))


def evaluate_market_radar(
    symbol: str,
    daily: list[Candle],
    four_hour: list[Candle],
    hourly: list[Candle],
    fifteen_minute: list[Candle],
) -> list[RadarAlert]:
    """Return independent alerts for any of the five agreed radar categories.

    At least 30 closed bars per timeframe are required. ATH/ATL calculations
    use the available daily sample, not a claim of complete exchange history.
    """
    if min(map(len, (daily, four_hour, hourly, fifteen_minute))) < 30:
        return []

    alerts: list[RadarAlert] = []
    d, h, m = daily[-1], hourly[-1], fifteen_minute[-1]
    vol = _volume_ratio(fifteen_minute)
    compressed = _range_compression(hourly)
    bull = _bullish_structure(fifteen_minute)
    bear = _bearish_structure(fifteen_minute)
    sweep_down = _sweep_low(fifteen_minute)
    sweep_up = _sweep_high(fifteen_minute)
    prior_high = max(bar.high for bar in fifteen_minute[-21:-1])
    prior_low = min(bar.low for bar in fifteen_minute[-21:-1])
    bullish_break = m.close > prior_high
    bearish_break = m.close < prior_low

    # 1) Early / pre-expansion accumulation.
    score, why = 0, []
    if compressed:
        score += 25
        why.append("فشردگی دامنه نوسان در ۱ساعته")
    if 1.25 <= vol < 1.8:
        score += 20
        why.append("افزایش تدریجی حجم ۱۵دقیقه")
    if m.close >= prior_high * 0.985:
        score += 20
        why.append("قیمت نزدیک سقف محدوده کوتاه‌مدت")
    if four_hour[-1].close > four_hour[-6].close:
        score += 15
        why.append("تمایل صعودی در ۴ساعته")
    if not bullish_break and not bearish_break:
        score += 10
        why.append("هنوز شکست نهایی رخ نداده")
    item = _alert(symbol, "EARLY", "BUY", score, m, why)
    if item:
        alerts.append(item)

    # 2) Bullish breakout / start of upward move.
    score, why = 0, []
    if bullish_break:
        score += 35
        why.append("شکست سقف ۲۰ کندل قبلی ۱۵دقیقه")
    if vol >= 1.8:
        score += 25
        why.append("حجم شکست بالاتر از میانگین")
    if bull:
        score += 20
        why.append("تغییر ساختار کوتاه‌مدت صعودی")
    if _sweep_low(fifteen_minute[-30:]):
        score += 10
        why.append("جمع‌آوری نقدینگی زیر کف اخیر")
    if four_hour[-1].close > four_hour[-6].close:
        score += 10
        why.append("هم‌جهتی حرکت با مومنتوم ۴ساعته")
    item = _alert(symbol, "BULLISH_BREAKOUT", "BUY", score, m, why)
    if item:
        alerts.append(item)

    # 3) Near the highest high in the available daily history.
    available_high = max(bar.high for bar in daily)
    distance_high = (available_high - d.close) / available_high if available_high > 0 else 1
    upper_rejection = (d.high - max(d.open, d.close)) / max(d.high - d.low, 1e-12)
    score, why = 0, []
    if distance_high <= 0.05:
        score += 40
        why.append("نزدیک بالاترین سقف در تاریخچه روزانه موجود")
    if upper_rejection >= 0.45:
        score += 20
        why.append("سایه بالایی و رد قیمت از سقف")
    if bear or sweep_up:
        score += 20
        why.append("نشانه ضعف ساختار یا جمع‌آوری نقدینگی بالای سقف")
    if vol >= 1.5 and d.close < d.open:
        score += 15
        why.append("افزایش حجم همراه با کندل روزانه نزولی")
    if four_hour[-1].close < four_hour[-6].close:
        score += 10
        why.append("ضعف مومنتوم ۴ساعته")
    item = _alert(symbol, "ATH_WARNING", "SELL", score, d, why)
    if item:
        alerts.append(item)

    # 4) Bearish breakdown / start of decline.
    score, why = 0, []
    if bearish_break:
        score += 35
        why.append("شکست کف ۲۰ کندل قبلی ۱۵دقیقه")
    if vol >= 1.8:
        score += 25
        why.append("افزایش حجم هنگام شکست حمایت")
    if bear:
        score += 20
        why.append("تغییر ساختار کوتاه‌مدت نزولی")
    if sweep_up:
        score += 10
        why.append("جمع‌آوری نقدینگی بالای سقف اخیر")
    if four_hour[-1].close < four_hour[-6].close:
        score += 10
        why.append("هم‌جهتی مومنتوم ۴ساعته با نزول")
    item = _alert(symbol, "BEARISH_BREAKDOWN", "SELL", score, m, why)
    if item:
        alerts.append(item)

    # 5) Possible reversal from the lowest low in available daily history.
    available_low = min(bar.low for bar in daily)
    distance_low = (d.close - available_low) / max(available_low, 1e-12)
    lower_rejection = (min(d.open, d.close) - d.low) / max(d.high - d.low, 1e-12)
    score, why = 0, []
    if distance_low <= 0.05:
        score += 40
        why.append("نزدیک پایین‌ترین کف در تاریخچه روزانه موجود")
    if lower_rejection >= 0.45:
        score += 20
        why.append("سایه پایینی و رد قیمت از کف")
    if sweep_down:
        score += 15
        why.append("جمع‌آوری نقدینگی زیر کف اخیر")
    if bull:
        score += 15
        why.append("تغییر ساختار کوتاه‌مدت صعودی")
    if vol >= 1.5 and d.close > d.open:
        score += 10
        why.append("افزایش حجم همراه با کندل روزانه صعودی")
    item = _alert(symbol, "BOTTOM_REVERSAL", "BUY", score, d, why)
    if item:
        alerts.append(item)

    return sorted(alerts, key=lambda alert: (alert.score, alert.category), reverse=True)
