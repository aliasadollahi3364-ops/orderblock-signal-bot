"""Telegram status interface. This module cannot submit exchange orders."""
from __future__ import annotations

from dataclasses import asdict

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from app.runtime import runtime
from app.strategy import StrategyConfig


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
        "pivot15": "پیوت ۱۵دقیقه", "max_wait": "حداکثر انتظار CHoCH",
        "atr_len": "دوره ATR", "use_trend_filter": "فیلتر روند",
        "require_ob_touch": "الزام برخورد به OB", "use_touch_freshness": "تازگی برخورد",
        "max_bars_since_touch": "حداکثر کندل از برخورد", "use_choch_filter": "فیلتر CHoCH",
        "use_choch_strength": "قدرت CHoCH", "min_choch_body_atr": "حداقل بدنه CHoCH/ATR",
        "use_retest_filter": "فیلتر Retest", "retest_atr": "تلورانس Retest/ATR",
        "use_confirm_candle": "کندل تأیید", "use_confirm_strength": "قدرت کندل تأیید",
        "min_confirm_body_atr": "حداقل بدنه تأیید/ATR", "sl_buffer_atr": "بافر حدضرر/ATR",
        "use_ob_boundary_sl": "حدضرر مرز OB", "sma_length": "دوره SMA",
        "use_sma200_filter": "فیلتر SMA200", "use_break_even": "حدضرر سر به سر",
        "break_even_trigger_r": "فعال‌سازی BE برحسب R", "use_trailing_stop": "تریلینگ استاپ",
        "trailing_atr_mult": "ضریب تریلینگ", "min_tick": "حداقل گام قیمت",
    }
    lines = ["⚙️ تنظیمات استراتژی اسکنر", "منبع تنظیمات: StrategyConfig فعلی ربات"]
    for key, value in asdict(cfg).items():
        shown = "روشن" if value is True else "خاموش" if value is False else str(value)
        lines.append(f"{labels.get(key, key)}: {shown}")
    lines.append("\nتایم‌فریم اردربلاک: 4H | تایم‌فریم ورود: 15m")
    lines.append("توجه: این خروجی مقادیر پیش‌فرض موتور استراتژی را نشان می‌دهد.")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    await update.effective_message.reply_text(
        "ربات سیگنال اردربلاک آماده است. این نسخه فقط حساب دمو دارد.\n"
        "دستورات: /status /demo /open /stats /pairs /add /remove /auto_pairs /strategy /set /enable /disable /reset_strategy /help"
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
    requested = " ".join(context.args).strip().upper()
    if not requested.endswith("/USDT"):
        requested += "/USDT"
    if requested not in runtime.manual_symbols:
        await update.effective_message.reply_text(
            "این ارز در فهرست دستی نیست. حذف فقط برای ارزهایی انجام می‌شود که خودت اضافه کرده‌ای."
        )
        return
    runtime.manual_symbols.remove(requested)
    if runtime.storage is not None:
        runtime.storage.save_manual_symbols(runtime.manual_symbols)
    await update.effective_message.reply_text(f"🗑 {requested} از فهرست دستی حذف شد.")


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
            + "\n\nبرای تغییر: /enable sma200 یا /disable sma200"
            + "\nبرای عددها: /set pivot4 4"
            + "\nبازنشانی: /reset_strategy"
        )


STRATEGY_ALIASES = {
    "trend": "use_trend_filter",
    "ob_touch": "require_ob_touch",
    "touch_freshness": "use_touch_freshness",
    "choch": "use_choch_filter",
    "choch_strength": "use_choch_strength",
    "retest": "use_retest_filter",
    "confirm_candle": "use_confirm_candle",
    "confirm_strength": "use_confirm_strength",
    "ob_boundary_sl": "use_ob_boundary_sl",
    "sma200": "use_sma200_filter",
    "break_even": "use_break_even",
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
            "نام گزینه را وارد کن. مثال: /enable sma200 یا /disable trailing"
        )
        return
    key = STRATEGY_ALIASES.get(context.args[0].lower(), context.args[0].lower())
    values = asdict(runtime.strategy_config or StrategyConfig())
    if key not in values or not isinstance(values[key], bool):
        await update.effective_message.reply_text(
            "گزینه روشن/خاموش پیدا نشد. نام‌های کوتاه: trend, ob_touch, choch, retest, "
            "confirm_candle, sma200, break_even, trailing"
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
        "/open - معاملات باز\n/pairs - فهرست ارزها\n/add BTC - افزودن ارز دستی\n/remove BTC - حذف ارز دستی\n/auto_pairs - وضعیت انتخاب خودکار\n/stats - آمار حساب\n/strategy - نمایش تنظیمات\n/set pivot4 4 - تغییر عدد\n/enable sma200 - روشن کردن فیلتر\n/disable sma200 - خاموش کردن فیلتر\n/reset_strategy - بازنشانی\n/help - راهنما"
    )


def build_telegram_application(token: str, allowed_chat_id: str) -> Application:
    if not allowed_chat_id:
        raise ValueError("TELEGRAM_CHAT_ID is required to restrict bot access.")
    application = ApplicationBuilder().token(token).build()
    application.bot_data["allowed_chat_id"] = allowed_chat_id
    for command, callback in (
        ("start", start), ("status", status), ("demo", demo),
        ("open", open_positions), ("pairs", pairs), ("add", add_pair), ("remove", remove_pair), ("auto_pairs", auto_pairs), ("stats", stats), ("strategy", strategy),
        ("set", set_strategy_value), ("enable", enable_strategy), ("disable", disable_strategy), ("reset_strategy", reset_strategy), ("help", help_command),
    ):
        application.add_handler(CommandHandler(command, callback))
    return application
