"""Lightweight PNG candlestick chart for Telegram, using Pillow."""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

from PIL import Image, ImageDraw


def make_signal_chart(rows, symbol: str, side: str, entry: float, stop: float,
                      tp1: float, tp2: float, tp3: float) -> BytesIO:
    bars = [r for r in rows if len(r) >= 5 and float(r[2]) > 0 and float(r[3]) > 0]
    bars = bars[-70:]
    if not bars:
        raise ValueError("No candle data for chart")
    width, height = 1100, 720
    left, right, top, bottom = 90, 1040, 100, 630
    image = Image.new("RGB", (width, height), "#101820")
    draw = ImageDraw.Draw(image)
    prices = [float(r[i]) for r in bars for i in (2, 3)] + [entry, stop, tp1, tp2, tp3]
    lo, hi = min(prices), max(prices)
    pad = max((hi - lo) * 0.08, abs(hi) * 0.0005)
    lo, hi = lo - pad, hi + pad
    def y(price):
        return int(bottom - (price - lo) / (hi - lo) * (bottom - top))
    draw.text((left, 25), f"{symbol}  |  {side}  |  15m Order Block signal", fill="white")
    colors = [("#26c281" if side == "BUY" else "#ef5350")]
    n = len(bars)
    step = (right - left) / n
    for i, row in enumerate(bars):
        x = int(left + (i + 0.5) * step)
        op, high, lowp, close = map(float, row[1:5])
        color = "#26c281" if close >= op else "#ef5350"
        draw.line((x, y(high), x, y(lowp)), fill=color, width=3)
        y1, y2 = sorted((y(op), y(close)))
        draw.rectangle((x - max(2, int(step * .28)), y1, x + max(2, int(step * .28)), max(y1 + 2, y2)), fill=color)
    levels = [
        ("ENTRY", entry, "#ffffff"), ("STOP", stop, "#ff5252"),
        ("TP1", tp1, "#00c853"), ("TP2", tp2, "#00c853"), ("TP3", tp3, "#00c853"),
    ]
    for label, price, color in levels:
        yy = y(price)
        draw.line((left, yy, right, yy), fill=color, width=2)
        draw.text((right - 80, max(top, yy - 18)), f"{label} {price:g}", fill=color)
    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out
