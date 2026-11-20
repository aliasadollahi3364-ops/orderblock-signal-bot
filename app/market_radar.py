"""Independent five-category market radar with top-down entry confirmation.

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
    entry_price: float | None = None
    stop_loss: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None


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


def _ema(values: list[float], length: int) -> float | None:
    if len(values) < length:
        return None
    alpha = 2.0 / (length + 1)
    value = _mean(values[:length])
    for item in values[length:]:
        value = alpha * item + (1 - alpha) * value
    return value


def _market_bias(daily: list[Candle], four_hour: list[Candle]) -> str:
    """Use D1 EMA20 and H4 six-candle slope; disagreeing frames mean neutral."""
    ema = _ema([bar.close for bar in daily], 20)
    if ema is None or len(four_hour) < 6:
        return "NEUTRAL"
    daily_up = daily[-1].close > ema
    daily_down = daily[-1].close < ema
    h4_up = four_hour[-1].close > four_hour[-6].close
    h4_down = four_hour[-1].close < four_hour[-6].close
    if daily_up and h4_up:
        return "BUY"
    if daily_down and h4_down:
        return "SELL"
    return "NEUTRAL"


def _apply_market_alignment(score: int, reasons: list[str], side: str, bias: str) -> int:
    if bias == side:
        reasons.append("هم‌جهتی با روند اصلی بازار در D1 و H4")
        return score + 12
    if bias in {"BUY", "SELL"}:
        reasons.append("ناهم‌جهتی با روند اصلی بازار؛ اولویت و امتیاز کاهش یافت")
        return score - 12
    reasons.append("روند اصلی بازار خنثی یا نامشخص است")
    return score


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
    prior, recent = bars[-8:-4], bars[-4:]
    return (
        min(bar.low for bar in recent) > min(bar.low for bar in prior)
        and max(bar.high for bar in recent) > max(bar.high for bar in prior)
        and recent[-1].close > recent[0].close
    )


def _bearish_structure(bars: list[Candle]) -> bool:
    if len(bars) < 8:
        return False
    prior, recent = bars[-8:-4], bars[-4:]
    return (
        max(bar.high for bar in recent) < max(bar.high for bar in prior)
        and min(bar.low for bar in recent) < min(bar.low for bar in prior)
        and recent[-1].close < recent[0].close
    )


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


def _five_minute_trigger(bars: list[Candle], side: str) -> bool:
    """Require a closed 5m candle to break the preceding three-candle range."""
    if len(bars) < 5:
        return False
    last = bars[-1]
    prior = bars[-4:-1]
    if side == "BUY":
        return last.close > last.open and last.close > max(bar.high for bar in prior)
    return last.close < last.open and last.close < min(bar.low for bar in prior)


def _top_down_confirmation(
    side: str,
    daily: list[Candle],
    four_hour: list[Candle],
    hourly: list[Candle],
    fifteen_minute: list[Candle],
    five_minute: list[Candle],
) -> tuple[bool, list[str]]:
    """D1 bias -> H4 direction -> H1 structure -> M15 setup -> M5 trigger."""
    daily_ema = _ema([bar.close for bar in daily], 20)
    if daily_ema is None:
        return False, []
    if side == "BUY":
        d1_ok = daily[-1].close > daily_ema
        h4_ok = four_hour[-1].close > four_hour[-6].close
        h1_ok = _bullish_structure(hourly)
        m15_ok = (
            _bullish_structure(fifteen_minute)
            or _sweep_low(fifteen_minute)
            or fifteen_minute[-1].close > max(bar.high for bar in fifteen_minute[-21:-1])
        )
    else:
        d1_ok = daily[-1].close < daily_ema
        h4_ok = four_hour[-1].close < four_hour[-6].close
        h1_ok = _bearish_structure(hourly)
        m15_ok = (
            _bearish_structure(fifteen_minute)
            or _sweep_high(fifteen_minute)
            or fifteen_minute[-1].close < min(bar.low for bar in fifteen_minute[-21:-1])
        )
    m5_ok = _five_minute_trigger(five_minute, side)
    reasons = []
    if d1_ok:
        reasons.append("جهت روزانه همسو")
    if h4_ok:
        reasons.append("جهت ۴ساعته همسو")
    if h1_ok:
        reasons.append("ساختار ۱ساعته همسو")
    if m15_ok:
        reasons.append("آماده‌سازی ورود در ۱۵دقیقه")
    if m5_ok:
        reasons.append("تریگر ورود در کندل بسته‌شده ۵دقیقه")
    return d1_ok and h4_ok and h1_ok and m15_ok and m5_ok, reasons


def _entry_plan(side: str, bars: list[Candle]) -> tuple[float, float, float, float, float] | None:
    """Build a compact structural stop and 2R/3R/4R targets from closed M5 bars."""
    if len(bars) < 15:
        return None
    entry = bars[-1].close
    true_ranges = []
    for index in range(1, len(bars)):
        previous_close = bars[index - 1].close
        bar = bars[index]
        true_ranges.append(max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        ))
    atr = _mean(true_ranges[-14:])
    if atr <= 0:
        return None
    recent = bars[-6:]
    buffer = atr * 0.15
    stop = min(bar.low for bar in recent) - buffer if side == "BUY" else max(bar.high for bar in recent) + buffer
    risk = entry - stop if side == "BUY" else stop - entry
    # Avoid calling a wide/poorly located stop a precision entry.
    if risk <= 0 or risk / entry > 0.015:
        return None
    sign = 1 if side == "BUY" else -1
    return entry, stop, entry + sign * 2 * risk, entry + sign * 3 * risk, entry + sign * 4 * risk


def _alert(
    symbol: str,
    category: str,
    side: str,
    score: int,
    bar: Candle,
    reasons: list[str],
    allow_confirmed: bool = False,
    entry_bars: list[Candle] | None = None,
) -> RadarAlert | None:
    stage = _stage(score)
    if stage is None:
        return None
    if stage == "CONFIRMED" and not allow_confirmed:
        stage = "PRE_SIGNAL"
    # A conditional plan is useful before confirmation too; the Telegram text
    # must label it as a reference, never as an immediate entry instruction.
    plan = _entry_plan(side, entry_bars or [])
    if stage == "CONFIRMED" and plan is None:
        stage = "PRE_SIGNAL"
    reference_bar = entry_bars[-1] if stage == "CONFIRMED" and entry_bars else bar
    return RadarAlert(
        symbol, category, side, stage, min(100, score),
        reference_bar.timestamp, reference_bar.close, tuple(reasons),
        *(plan if plan is not None and stage == "CONFIRMED" else (None, None, None, None, None)),
    )


def evaluate_market_radar(
    symbol: str,
    daily: list[Candle],
    four_hour: list[Candle],
    hourly: list[Candle],
    fifteen_minute: list[Candle],
    five_minute: list[Candle] | None = None,
    bullish_ob_near: bool = False,
    bearish_ob_near: bool = False,
) -> list[RadarAlert]:
    """Evaluate five independent radar categories using closed candles only.

    D1/H4/H1 define directional context. M15 prepares the setup and a closed
    M5 breakout/rejection candle is mandatory before any category can become
    CONFIRMED. ATH/ATL use the available daily sample, not complete history.
    """
    five_minute = five_minute or []
    if min(map(len, (daily, four_hour, hourly, fifteen_minute))) < 30:
        return []

    alerts: list[RadarAlert] = []
    d, m = daily[-1], fifteen_minute[-1]
    market_bias = _market_bias(daily, four_hour)
    vol = _volume_ratio(fifteen_minute)
    daily_vol = _volume_ratio(daily)
    compressed = _range_compression(hourly)
    bull = _bullish_structure(fifteen_minute)
    bear = _bearish_structure(fifteen_minute)
    sweep_down = _sweep_low(fifteen_minute)
    sweep_up = _sweep_high(fifteen_minute)
    prior_high = max(bar.high for bar in fifteen_minute[-21:-1])
    prior_low = min(bar.low for bar in fifteen_minute[-21:-1])
    bullish_break = m.close > prior_high
    bearish_break = m.close < prior_low

    bull_confirmed, bull_tf_reasons = _top_down_confirmation(
        "BUY", daily, four_hour, hourly, fifteen_minute, five_minute
    )
    bear_confirmed, bear_tf_reasons = _top_down_confirmation(
        "SELL", daily, four_hour, hourly, fifteen_minute, five_minute
    )

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
    if bullish_ob_near:
        score += 10
        why.append("نزدیکی قیمت به اردربلاک صعودی فعال ۴ساعته")
    if not bullish_break and not bearish_break:
        score += 10
        why.append("هنوز شکست نهایی رخ نداده")
    score = _apply_market_alignment(score, why, "BUY", market_bias)
    item = _alert(symbol, "EARLY", "BUY", score, m, why, bull_confirmed, five_minute)
    if item:
        alerts.append(item)

    # 2) Bullish breakout / start of upward move.
    score, why = 0, []
    if bullish_break:
        score += 35
        why.append("شکست سقف ۲۰ کندل قبلی ۱۵دقیقه")
    elif m.close >= prior_high * 0.985 and bull:
        score += 22
        why.append("هشدار زودهنگام: قیمت به مقاومت نزدیک شده و ساختار صعودی است؛ هنوز شکست رخ نداده")
    if vol >= 1.8:
        score += 25
        why.append("حجم شکست بالاتر از میانگین")
    if bull:
        score += 20
        why.append("تغییر ساختار کوتاه‌مدت صعودی")
    if sweep_down:
        score += 10
        why.append("جمع‌آوری نقدینگی زیر کف اخیر")
    if four_hour[-1].close > four_hour[-6].close:
        score += 10
        why.append("هم‌جهتی حرکت با مومنتوم ۴ساعته")
    if bullish_ob_near:
        score += 10
        why.append("وجود اردربلاک صعودی فعال ۴ساعته نزدیک قیمت")
    why.extend(bull_tf_reasons if bull_confirmed else [])
    score = _apply_market_alignment(score, why, "BUY", market_bias)
    item = _alert(symbol, "BULLISH_BREAKOUT", "BUY", score, m, why, bull_confirmed, five_minute)
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
    if daily_vol >= 1.5 and d.close < d.open:
        score += 15
        why.append("افزایش حجم همراه با کندل روزانه نزولی")
    if four_hour[-1].close < four_hour[-6].close:
        score += 10
        why.append("ضعف مومنتوم ۴ساعته")
    if bearish_ob_near:
        score += 10
        why.append("نزدیکی قیمت به اردربلاک نزولی فعال ۴ساعته")
    why.extend(bear_tf_reasons if bear_confirmed else [])
    score = _apply_market_alignment(score, why, "SELL", market_bias)
    item = _alert(symbol, "ATH_WARNING", "SELL", score, d, why, bear_confirmed, five_minute)
    if item:
        alerts.append(item)

    # 4) Bearish breakdown / start of decline.
    score, why = 0, []
    if bearish_break:
        score += 35
        why.append("شکست کف ۲۰ کندل قبلی ۱۵دقیقه")
    elif m.close <= prior_low * 1.015 and bear:
        score += 22
        why.append("هشدار زودهنگام: قیمت به حمایت نزدیک شده و ساختار نزولی است؛ هنوز شکست رخ نداده")
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
    if bearish_ob_near:
        score += 10
        why.append("وجود اردربلاک نزولی فعال ۴ساعته نزدیک قیمت")
    why.extend(bear_tf_reasons if bear_confirmed else [])
    score = _apply_market_alignment(score, why, "SELL", market_bias)
    item = _alert(symbol, "BEARISH_BREAKDOWN", "SELL", score, m, why, bear_confirmed, five_minute)
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
    if daily_vol >= 1.5 and d.close > d.open:
        score += 10
        why.append("افزایش حجم همراه با کندل روزانه صعودی")
    if bullish_ob_near:
        score += 10
        why.append("نزدیکی قیمت به اردربلاک صعودی فعال ۴ساعته")
    why.extend(bull_tf_reasons if bull_confirmed else [])
    score = _apply_market_alignment(score, why, "BUY", market_bias)
    item = _alert(symbol, "BOTTOM_REVERSAL", "BUY", score, d, why, bull_confirmed, five_minute)
    if item:
        alerts.append(item)

    return sorted(alerts, key=lambda alert: (alert.score, alert.category), reverse=True)
