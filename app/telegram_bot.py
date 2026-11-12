"""Telegram status interface. This module cannot submit exchange orders."""
from __future__ import annotations

from dataclasses import asdict
from io import BytesIO

from PIL import Image, ImageDraw

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from app.runtime import runtime
from app.strategy import StrategyConfig

import asyncio
from telegram.error import TelegramError

TEMP_MESSAGE_TTL_SECONDS = 120


async def _delete_later(bot, chat_id: int, message_id: int) -> None:
    await asyncio.sleep(TEMP_MESSAGE_TTL_SECONDS)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError:
        pass


async def _reply_temporary(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    message = await _reply_temporary(update, context, text, **kwargs)
    context.application.create_task(
        _delete_later(context.bot, message.chat_id, message.message_id),
        name=f"delete-temporary-{message.message_id}",
    )
    return message


async def _send_temporary_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, photo, caption: str):
    message = await update.effective_message.reply_photo(photo=photo, caption=caption)
    context.application.create_task(
        _delete_later(context.bot, message.chat_id, message.message_id),
        name=f"delete-temporary-photo-{message.message_id}",
    )
    return message




async def _authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    allowed_chat_id = context.application.bot_data.get("allowed_chat_id")
    actual_chat_id = update.effective_chat.id if update.effective_chat else None
    if not allowed_chat_id or str(actual_chat_id) != str(allowed_chat_id):
        if update.effective_message:
            await update.effective_message.reply_text("دسترسی مجاز نیست.")
        return False
    return True


def _account_text() -> str:
    acct = runtime.account
    return (
        "حساب دمو\n"
        f"موجودی اولیه: {acct.initial_balance:.2f} USDT\n"
        f"موجودی: {float(acct.balance or 0):.2f} USDT\n"
        f"مارجین هر معامله: {acct.margin_per_trade:.2f} USDT\n"
        f"اهرم: {acct.leverage:g}x\n"
        f"ارزش اسمی هر معامله: {acct.notional:.2f} USDT\n"
        f"مارجین مصرف‌شده: {acct.margin_used:.2f} USDT\n"
        f"مارجین آزاد: {acct.free_margin:.2f} USDT\n"
        f"معاملات باز: {len(acct.open_positions)}\n"
        "معاملات واقعی: غیرفعال"
    )


def _strategy_text() -> str:
    cfg = runtime.strategy_config or StrategyConfig()
    labels = {
        "pivot4": "پیوت ۴ساعته", "body_len": "میانگین بدنه کندل ۴ساعته",
        "ob_search": "جستجوی کندل اردربلاک", "max_stored_ob": "حداکثر OB ذخیره",
        "ob_atr_len": "دوره ATR کیفیت OB", "min_bos_body_atr": "حداقل بدنه BOS/ATR",
        "min_bos_body_avg": "حداقل بدنه BOS/میانگین", "min_break_atr": "حداقل فاصله شکست/ATR",
        "max_ob_candle_range_atr": "حداکثر دامنه کندل OB/ATR",
        "pivot15": "پیوت ۱۵دقیقه", "max_wait": "حداکثر انتظار CHoCH",
        "atr_len": "دوره ATR", "use_trend_filter": "فیلتر روند",
        "require_ob_touch": "الزام برخورد به OB", "use_touch_freshness": "تازگی برخورد",
        "max_bars_since_touch": "حداکثر کندل از برخورد", "use_choch_filter": "فیلتر CHoCH",
        "use_choch_strength": "قدرت CHoCH", "min_choch_body_atr": "حداقل بدنه CHoCH/ATR",
        "use_retest_filter": "فیلتر Retest", "retest_atr": "تلورانس Retest/ATR",
        "use_confirm_candle": "کندل تأیید", "use_confirm_strength": "قدرت کندل تأیید",
        "min_confirm_body_atr": "حداقل بدنه تأیید/ATR",
        "enable_trendline_signals": "فعال‌بودن سیگنال خط روند",
        "use_sma200_trendline_filter": "فیلتر SMA200 خط روند",
        "require_trendline_pullback": "الزام پولبک خط روند",
        "trendline_retest_atr": "تلورانس پولبک خط روند/ATR",
        "trendline_pullback_max_wait": "مهلت پولبک خط روند",
        "risk_percent": "ریسک سرمایه درصد", "risk_reward": "نسبت ریسک به سود",
        "sl_buffer_atr": "بافر حدضرر/ATR",
        "use_ob_boundary_sl": "حدضرر مرز OB", "sma_length": "دوره SMA",
        "use_sma200_filter": "فیلتر SMA200 اردربلاک", "use_trailing_stop": "تریلینگ استاپ",
        "trailing_atr_mult": "ضریب تریلینگ", "min_tick": "حداقل گام قیمت",
    }
    lines = ["⚙️ تنظیمات استراتژی", "دو مسیر ورود: اردربلاک ۱۵ دقیقه و خط روند ۱۵ دقیقه", "منبع روند و محدوده مرجع: اردربلاک ۴ ساعته"]
    for key, value in asdict(cfg).items():
        shown = "روشن" if value is True else "خاموش" if value is False else str(value)
        lines.append(f"{labels.get(key, key)}: {shown}")
    lines.append("\nتایم‌فریم مرجع: 4H | تایم‌فریم صدور هر دو نوع سیگنال: 15m")
    lines.append("توجه: این خروجی مقادیر پیش‌فرض موتور استراتژی را نشان می‌دهد.")
    return "\n".join(lines)


MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["/status", "/demo"],
        ["/open", "/stats"],
        ["/panel", "/history"],
        ["مدیریت ارزها", "تنظیمات استراتژی"],
        ["/help", "/menu"],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="یک بخش را انتخاب کن...",
)


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    await update.effective_message.reply_text(
        "🏠 منوی اصلی ربات\nبرای افزودن ارز: /add BTC | برای حذف: /remove BTC",
        reply_markup=MAIN_MENU,
    )


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    text = (update.effective_message.text or "").strip()
    if text == "مدیریت ارزها":
        answer = "🪙 مدیریت ارزها\n/pairs فهرست ارزها\n/auto_pairs وضعیت انتخاب خودکار\n/add BTC افزودن ارز\n/remove BTC حذف ارز دستی"
    elif text == "تنظیمات استراتژی":
        answer = "⚙️ تنظیمات استراتژی\n/strategy نمایش همه تنظیمات\n/enable trend روشن کردن فیلتر روند ۴ساعته OB\n/enable trendline فعال‌کردن سیگنال خط روند\n/disable trendline غیرفعال‌کردن سیگنال خط روند\n/enable trendline_pullback الزام پولبک خط روند\n/set pivot4 4 تغییر مقدار عددی\n/reset_strategy بازنشانی تنظیمات"
    else:
        answer = "برای استفاده از منو، یکی از دکمه‌ها را انتخاب کن یا /menu را بفرست."
    await update.effective_message.reply_text(answer, reply_markup=MAIN_MENU)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    await update.effective_message.reply_text(
        "ربات سیگنال اردربلاک آماده است. این نسخه فقط حساب دمو دارد.\n"
        "از دکمه‌های پایین برای دسترسی به بخش‌ها استفاده کن.",
        reply_markup=MAIN_MENU,
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    last_scan = runtime.last_scan_at.isoformat() if runtime.last_scan_at else "هنوز اجرا نشده"
    await update.effective_message.reply_text(
        f"وضعیت: {runtime.scanner_state}\n"
        f"آخرین اسکن: {last_scan}\n"
        f"تعداد نمادهای انتخاب‌شده: {len(runtime.selected_symbols)}\n"
        f"تعداد سیگنال‌ها: {runtime.signal_count}\n"
        "حالت: فقط دمو"
    )


async def demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _authorized(update, context):
        await update.effective_message.reply_text(_account_text())


async def open_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    positions = runtime.account.open_positions
    if not positions:
        await update.effective_message.reply_text("در حال حاضر معامله دمو بازی وجود ندارد.")
        return
    lines = ["معاملات دمو باز:"]
    for p in positions:
        lines.append(
            f"{p.symbol} | {p.side.value}\n"
            f"ورود: {p.entry:g} | زمان ورود: {p.opened_at or 'نامشخص'}\nاستاپ فعلی: {p.stop:g}\n"
            f"TP1: {p.tp1:g} | TP2: {p.tp2:g} | TP3: {p.tp3:g}"
        )
    await update.effective_message.reply_text("\n\n".join(lines))


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    acct = runtime.account
    realized = sum(p.realized_pnl for p in acct.positions.values())
    closed = sum(1 for p in acct.positions.values() if p.closed)
    await update.effective_message.reply_text(
        f"آمار حساب دمو\nموجودی فعلی: {float(acct.balance or 0):.2f} USDT\n"
        f"سود/زیان ثبت‌شده معاملات: {realized:+.4f} USDT\n"
        f"معاملات بسته‌شده: {closed}\nمعاملات باز: {len(acct.open_positions)}"
    )



def _demo_panel_text() -> str:
    acct = runtime.account
    positions = list(acct.positions.values())
    closed = [p for p in positions if p.closed]
    wins = sum(1 for p in closed if p.realized_pnl > 0)
    losses = sum(1 for p in closed if p.realized_pnl < 0)
    breakeven = len(closed) - wins - losses
    realized = sum(p.realized_pnl for p in positions)
    win_rate = (wins / len(closed) * 100.0) if closed else 0.0
    pnl_pct = (realized / acct.initial_balance * 100.0) if acct.initial_balance else 0.0
    return (
        "📊 پنل حرفه‌ای حساب دمو\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 موجودی اولیه: {acct.initial_balance:.2f} USDT\n"
        f"💵 موجودی فعلی: {float(acct.balance or 0):.2f} USDT\n"
        f"📈 سود/زیان تحقق‌یافته: {realized:+.4f} USDT ({pnl_pct:+.2f}%)\n"
        f"🎯 درصد معاملات برنده: {win_rate:.1f}%\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🟢 معاملات سودده: {wins}\n🔴 معاملات زیان‌ده: {losses}\n"
        f"⚪ سربه‌سر: {breakeven}\n"
        f"📂 معاملات باز: {len(acct.open_positions)}\n"
        f"📁 معاملات بسته: {len(closed)}\n"
        f"🔒 مارجین مصرف‌شده: {acct.margin_used:.2f} USDT\n"
        f"🆓 مارجین آزاد: {acct.free_margin:.2f} USDT\n"
        f"⚙️ اندازه هر معامله: {acct.notional:.2f} USDT | اهرم: {acct.leverage:g}x\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "حساب آزمایشی است؛ معاملات واقعی غیرفعال."
    )


def _demo_equity_chart() -> BytesIO:
    acct = runtime.account
    events = sorted(runtime.closed_trade_events, key=lambda e: str(e.get("time", "")))
    balance = float(acct.initial_balance)
    points = [balance]
    last_realized_by_symbol = {}
    opened = set()
    for event in events:
        symbol = str(event.get("symbol", ""))
        kind = str(event.get("type", ""))
        if kind == "OPEN":
            balance -= acct.notional * acct.fee_rate
            last_realized_by_symbol[symbol] = 0.0
            opened.add(symbol)
            points.append(balance)
        elif symbol:
            current = float(event.get("realized_pnl", 0.0) or 0.0)
            previous = last_realized_by_symbol.get(symbol, 0.0)
            balance += current - previous
            last_realized_by_symbol[symbol] = current
            points.append(balance)
    if len(points) == 1:
        points.append(float(acct.balance or acct.initial_balance))
    width, height = 1000, 480
    image = Image.new("RGB", (width, height), "#101820")
    draw = ImageDraw.Draw(image)
    draw.text((40, 24), "DEMO ACCOUNT EQUITY", fill="#ffffff")
    draw.text((40, 54), f"Initial: {acct.initial_balance:.2f} USDT   Current: {float(acct.balance or 0):.2f} USDT", fill="#b8c7d9")
    left, right, top, bottom = 70, 950, 110, 410
    lo, hi = min(points), max(points)
    pad = max((hi - lo) * 0.15, abs(hi) * 0.002, 0.01)
    lo -= pad
    hi += pad
    def xy(index, value):
        x = left + (right-left) * index / max(1, len(points)-1)
        y = bottom - (value-lo) / (hi-lo) * (bottom-top)
        return int(x), int(y)
    draw.line((left, bottom, right, bottom), fill="#526273", width=1)
    coords = [xy(i, value) for i, value in enumerate(points)]
    if len(coords) > 1:
        draw.line(coords, fill="#28c98b", width=4)
    for x, y in coords:
        draw.ellipse((x-4, y-4, x+4, y+4), fill="#28c98b")
    draw.text((left, top-25), f"{hi:.2f}", fill="#b8c7d9")
    draw.text((left, bottom+10), f"{lo:.2f} USDT", fill="#b8c7d9")
    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


async def demo_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    await _send_temporary_photo(update, context, _demo_equity_chart(), _demo_panel_text())


async def trade_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    positions = sorted(runtime.account.positions.values(), key=lambda p: p.opened_at or "", reverse=True)
    if not positions:
        await _reply_temporary(update, context, "📜 هنوز معامله‌ای در حساب دمو ثبت نشده است.")
        return
    lines = ["📜 تاریخچه معاملات دمو"]
    for p in positions[:15]:
        state = "باز" if not p.closed else ("سود" if p.realized_pnl > 0 else "زیان" if p.realized_pnl < 0 else "سربه‌سر")
        lines.append(
            f"\n{p.symbol} | {'خرید' if p.side.value == 'LONG' else 'فروش'} | {state}\n"
            f"ورود: {p.entry:g} | حدضرر اولیه: {p.initial_stop:g}\n"
            f"TP1: {p.tp1:g} | TP2: {p.tp2:g} | TP3: {p.tp3:g}\n"
            f"سود/زیان تحقق‌یافته: {p.realized_pnl:+.4f} USDT\n"
            f"زمان ورود: {p.opened_at or 'نامشخص'}"
        )
    await _reply_temporary(update, context, "\n".join(lines))


async def pairs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    manual = sorted(runtime.manual_symbols)
    automatic = [symbol for symbol in runtime.selected_symbols if symbol not in set(manual)]
    lines = [
        "📋 فهرست ارزهای ربات",
        f"ارزهای واجد شرایط فعلی اسکن: {len(runtime.selected_symbols)}",
        f"انتخاب خودکار: {len(automatic)}",
        f"افزوده‌شده دستی: {len(manual)}",
        "",
        "ارزهای در حال اسکن:",
    ]
    lines.extend(runtime.selected_symbols or ["فعلاً هیچ ارزی واجد شرایط OB نیست."])
    lines.extend(["", "فهرست دستی:"])
    lines.extend(manual or ["موردی اضافه نشده است."])
    text = "\n".join(lines)
    for start in range(0, len(text), 3800):
        await update.effective_message.reply_text(text[start:start + 3800])


async def add_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("نماد را وارد کن. مثال: /add BTC یا /add BTC/USDT")
        return
    try:
        candidate = await __import__("asyncio").to_thread(
            _resolve_symbol, " ".join(context.args)
        )
        symbol = candidate.symbol
        if symbol in runtime.manual_symbols:
            await update.effective_message.reply_text(f"{symbol} از قبل در فهرست دستی است.")
            return
        runtime.manual_symbols.append(symbol)
        if runtime.storage is not None:
            runtime.storage.save_manual_symbols(runtime.manual_symbols)
        await update.effective_message.reply_text(
            f"✅ {symbol} به فهرست دستی اضافه شد. در اسکن بعدی بررسی می‌شود؛ "
            "فقط در صورت داشتن اردربلاک معتبر وارد اسکن ۱۵ دقیقه‌ای خواهد شد."
        )
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ افزودن ارز انجام نشد: {exc}")


async def remove_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("مثال: /remove BTC یا /remove BTC/USDT")
        return
    requested = " ".join(context.args).strip().upper().replace("-", "/").replace(" ", "")
    if "/" in requested:
        base, quote_part = requested.split("/", 1)
        quote = quote_part.split(":", 1)[0]
        if quote and quote != "USDT":
            await update.effective_message.reply_text("فقط نمادهای قرارداد USDT را می‌توان حذف کرد.")
            return
    else:
        base = requested[:-4] if requested.endswith("USDT") and len(requested) > 4 else requested
    matched = next(
        (symbol for symbol in runtime.manual_symbols
         if symbol.split("/", 1)[0].upper() == base),
        None,
    )
    if matched is None:
        await update.effective_message.reply_text(
            "این ارز در فهرست دستی نیست. حذف فقط برای ارزهایی انجام می‌شود که خودت اضافه کرده‌ای."
        )
        return
    runtime.manual_symbols.remove(matched)
    if runtime.storage is not None:
        runtime.storage.save_manual_symbols(runtime.manual_symbols)
    await update.effective_message.reply_text(f"🗑 {matched} از فهرست دستی حذف شد.")


def _resolve_symbol(requested: str):
    # Market-data instance is attached by the scanner bootstrap.
    resolver = runtime.market_symbol_resolver
    if resolver is None:
        raise RuntimeError("اتصال بازار هنوز آماده نیست؛ چند لحظه دیگر تلاش کن.")
    return resolver(requested)


async def auto_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _authorized(update, context):
        await update.effective_message.reply_text(
            f"انتخاب خودکار: فعال\nنمادهای واجد شرایط فعلی: {len(runtime.selected_symbols)}\n"
            f"افزوده‌شده دستی: {len(runtime.manual_symbols)}\n"
            "اسکنر فقط ارزهای دارای اردربلاک معتبر را نگه می‌دارد."
        )


async def strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _authorized(update, context):
        await update.effective_message.reply_text(
            _strategy_text()
            + "\n\n/enable trendline یا /disable trendline برای فعال/غیرفعال‌کردن مسیر خط روند"
            + "\n/enable trendline_sma برای فیلتر SMA200 خط روند"
            + "\nبرای عددها: /set pivot4 4"
            + "\nبازنشانی: /reset_strategy"
        )


STRATEGY_ALIASES = {
    "trend": "use_trend_filter",
    "trendline": "enable_trendline_signals",
    "trendline_sma": "use_sma200_trendline_filter",
    "trendline_pullback": "require_trendline_pullback",
    "ob_touch": "require_ob_touch",
    "touch_freshness": "use_touch_freshness",
    "choch": "use_choch_filter",
    "choch_strength": "use_choch_strength",
    "retest": "use_retest_filter",
    "confirm_candle": "use_confirm_candle",
    "confirm_strength": "use_confirm_strength",
    "ob_boundary_sl": "use_ob_boundary_sl",
    "sma200": "use_sma200_filter",
    "trailing": "use_trailing_stop",
}


def _save_strategy_config(config: StrategyConfig) -> None:
    runtime.strategy_config = config
    if runtime.storage is not None:
        runtime.storage.save_strategy_config(config)


async def set_strategy_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "فرمت: /set نام_تنظیم مقدار\nمثال: /set pivot4 4\n"
            "برای روشن/خاموش کردن از /enable و /disable استفاده کن."
        )
        return
    key = STRATEGY_ALIASES.get(context.args[0].lower(), context.args[0].lower())
    values = asdict(runtime.strategy_config or StrategyConfig())
    if key not in values:
        await update.effective_message.reply_text("نام تنظیم پیدا نشد. برای دیدن نام‌ها /strategy را بفرست.")
        return
    current = values[key]
    if isinstance(current, bool):
        await update.effective_message.reply_text("این گزینه روشن/خاموش است؛ از /enable یا /disable استفاده کن.")
        return
    try:
        value = int(context.args[1]) if isinstance(current, int) else float(context.args[1])
        if isinstance(value, float) and not __import__("math").isfinite(value):
            raise ValueError("عدد باید معتبر باشد.")
        values[key] = value
        updated = StrategyConfig(**values)
        if key in {"min_choch_body_atr", "min_confirm_body_atr", "sl_buffer_atr"} and not 0 <= value <= 5:
            raise ValueError("مقدار باید بین 0 تا 5 باشد.")
        if key in {"retest_atr"} and not 0 <= value <= 5:
            raise ValueError("مقدار باید بین 0 تا 5 باشد.")
        if key in {"break_even_trigger_r", "trailing_atr_mult"} and not 0 < value <= 20:
            raise ValueError("مقدار باید بیشتر از صفر و حداکثر 20 باشد.")
        if key == "min_tick" and value <= 0:
            raise ValueError("min_tick باید بیشتر از صفر باشد.")
        _save_strategy_config(updated)
        await update.effective_message.reply_text(f"✅ {key} روی {value} تنظیم شد و از اسکن بعدی اعمال می‌شود.")
    except (TypeError, ValueError) as exc:
        await update.effective_message.reply_text(f"❌ مقدار نامعتبر است: {exc}")


async def toggle_strategy_value(update: Update, context: ContextTypes.DEFAULT_TYPE, enabled: bool) -> None:
    if not await _authorized(update, context):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "نام گزینه را وارد کن. مثال: /enable trendline یا /disable trendline_pullback"
        )
        return
    key = STRATEGY_ALIASES.get(context.args[0].lower(), context.args[0].lower())
    values = asdict(runtime.strategy_config or StrategyConfig())
    if key not in values or not isinstance(values[key], bool):
        await update.effective_message.reply_text(
            "گزینه روشن/خاموش پیدا نشد. نام‌های کوتاه: trend, trendline, trendline_sma, trendline_pullback, "
            "ob_touch, choch, retest, confirm_candle, sma200, trailing"
        )
        return
    values[key] = enabled
    _save_strategy_config(StrategyConfig(**values))
    state = "روشن" if enabled else "خاموش"
    await update.effective_message.reply_text(f"✅ {key}: {state}؛ از اسکن بعدی اعمال می‌شود.")


async def enable_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await toggle_strategy_value(update, context, True)


async def disable_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await toggle_strategy_value(update, context, False)


async def reset_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    config = StrategyConfig()
    _save_strategy_config(config)
    await update.effective_message.reply_text("تنظیمات استراتژی به پیش‌فرض کد بازگشت و ذخیره شد.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    await update.effective_message.reply_text(
        "/start - شروع\n/status - وضعیت ربات\n/demo - موجودی دمو\n"
        "/open - معاملات باز\n/pairs - فهرست ارزها\n/add BTC - افزودن ارز دستی\n"
        "/remove BTC - حذف ارز دستی\n/auto_pairs - وضعیت انتخاب خودکار\n/stats - آمار حساب\n"
        "/strategy - نمایش تنظیمات\n/set pivot4 4 - تغییر عدد\n"
        "/enable trendline - فعال‌کردن سیگنال خط روند\n/disable trendline - غیرفعال‌کردن آن\n"
        "/enable trendline_sma - فیلتر SMA200 خط روند\n"
        "/enable trendline_pullback - الزام پولبک خط روند\n"
        "/enable sma200 - فیلتر SMA200 اردربلاک\n/reset_strategy - بازنشانی\n/help - راهنما"
    )


def build_telegram_application(token: str, allowed_chat_id: str) -> Application:
    if not allowed_chat_id:
        raise ValueError("TELEGRAM_CHAT_ID is required to restrict bot access.")
    application = ApplicationBuilder().token(token).build()
    application.bot_data["allowed_chat_id"] = allowed_chat_id
    for command, callback in (
        ("start", start), ("menu", menu), ("status", status), ("demo", demo),
        ("open", open_positions), ("panel", demo_panel), ("history", trade_history), ("pairs", pairs), ("add", add_pair), ("remove", remove_pair), ("auto_pairs", auto_pairs), ("stats", stats), ("strategy", strategy),
        ("set", set_strategy_value), ("enable", enable_strategy), ("disable", disable_strategy), ("reset_strategy", reset_strategy), ("help", help_command),
    ):
        application.add_handler(CommandHandler(command, callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))
    return application
