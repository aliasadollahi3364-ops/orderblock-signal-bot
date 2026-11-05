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
    cfg = StrategyConfig()
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
        "دستورات: /status /demo /open /stats /strategy /help"
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


async def strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _authorized(update, context):
        await update.effective_message.reply_text(_strategy_text())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    await update.effective_message.reply_text(
        "/start - شروع\n/status - وضعیت ربات\n/demo - موجودی دمو\n"
        "/open - معاملات باز\n/stats - آمار حساب\n/strategy - تنظیمات استراتژی\n/help - راهنما"
    )


def build_telegram_application(token: str, allowed_chat_id: str) -> Application:
    if not allowed_chat_id:
        raise ValueError("TELEGRAM_CHAT_ID is required to restrict bot access.")
    application = ApplicationBuilder().token(token).build()
    application.bot_data["allowed_chat_id"] = allowed_chat_id
    for command, callback in (
        ("start", start), ("status", status), ("demo", demo),
        ("open", open_positions), ("stats", stats), ("strategy", strategy),
        ("help", help_command),
    ):
        application.add_handler(CommandHandler(command, callback))
    return application
