"""Telegram status interface. This module cannot submit exchange orders."""
from __future__ import annotations

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from app.runtime import runtime


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    await update.effective_message.reply_text(
        "ربات سیگنال اردربلاک آماده است. این نسخه فقط حساب دمو دارد.\n"
        "دستورات: /status /demo /open /stats /help"
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
    if not await _authorized(update, context):
        return
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
            f"ورود: {p.entry:g} | استاپ: {p.stop:g}\n"
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
        f"آمار حساب دمو\n"
        f"موجودی فعلی: {float(acct.balance or 0):.2f} USDT\n"
        f"سود/زیان ثبت‌شده معاملات: {realized:+.4f} USDT\n"
        f"معاملات بسته‌شده: {closed}\n"
        f"معاملات باز: {len(acct.open_positions)}"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    await update.effective_message.reply_text(
        "/start - شروع\n/status - وضعیت ربات\n/demo - موجودی و مارجین دمو\n"
        "/open - معاملات باز\n/stats - آمار حساب\n/help - راهنما"
    )


def build_telegram_application(token: str, allowed_chat_id: str) -> Application:
    if not allowed_chat_id:
        raise ValueError("TELEGRAM_CHAT_ID is required to restrict bot access.")
    application = ApplicationBuilder().token(token).build()
    application.bot_data["allowed_chat_id"] = allowed_chat_id
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("demo", demo))
    application.add_handler(CommandHandler("open", open_positions))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("help", help_command))
    return application
