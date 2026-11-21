"""Human-readable Persian Telegram messages for durable demo notifications."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def format_notification(item: dict[str, Any]) -> str:
    event = item["event"]
    kind = item["kind"]
    event_type = str(event.get("type", ""))
    raw_event_time = event.get("time", "نامشخص")
    event_time_text = (
        datetime.fromtimestamp(raw_event_time / 1000, timezone.utc).isoformat()
        if isinstance(raw_event_time, (int, float))
        else str(raw_event_time)
    )

    if kind == "pending" or event_type == "LIMIT_CANCELLED":
        return (
            f"❌ لیمیت شماره {event['leg']} لغو شد | {event['symbol']}\n"
            f"جهت: {event.get('side', 'نامشخص')}\n"
            f"قیمت سفارش: {float(event['price']):g}\n"
            f"زمان: {event_time_text}\n"
            f"علت لغو: {event.get('reason', 'نامشخص')}\n"
            "حساب دمو؛ معاملات واقعی خاموش"
        )
    if event_type == "LIMIT_FILLED":
        return (
            f"✅ لیمیت شماره {event['leg']} پر شد | {event['symbol']}\n"
            f"جهت: {event['side']}\n"
            f"قیمت پرشدن این لیمیت: {float(event['price']):g}\n"
            f"میانگین ورود پوزیشن واحد: {float(event['average_entry']):g}\n"
            f"مارجین استفاده‌شده: {float(event['margin_allocated']):g} دلار\n"
            f"اهداف مشترک: TP1 {float(event['tp1']):g} | TP2 {float(event['tp2']):g} | TP3 {float(event['tp3']):g}\n"
            f"زمان: {event_time_text}\nحساب دمو؛ معاملات واقعی خاموش"
        )

    labels = {
        "TP1": "🎯 هدف اول (TP1) زده شد",
        "TP2": "🎯 هدف دوم (TP2) زده شد",
        "TP3": "🏁 هدف سوم (TP3) زده شد؛ معامله کامل بسته شد",
        "STOP": "🛑 حد ضرر (SL) فعال شد؛ معامله بسته شد",
        "BREAK_EVEN_STOP": "⚖️ حد سربه‌سر فعال شد؛ معامله بسته شد",
        "TP1_TRAILING_STOP": "🔒 حد محافظتی بعد از TP2 فعال شد؛ معامله بسته شد",
    }
    label = labels.get(event_type, f"رویداد معامله: {event_type}")
    return (
        f"{label} | {event['symbol']}\n"
        f"جهت: {event.get('side', 'نامشخص')}\n"
        f"قیمت رویداد: {float(event['price']):g}\n"
        f"زمان: {event_time_text}\n"
        f"سود/زیان تحقق‌یافته تا این لحظه: {float(event.get('realized_pnl', 0.0)):+.4f} USDT\n"
        "حساب دمو | معاملات واقعی: خاموش"
    )
