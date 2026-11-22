"""Telegram interface for the preserved demo account and Iranian 18k gold module."""
from __future__ import annotations

import asyncio
import logging

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from app.runtime import runtime

logger = logging.getLogger(__name__)
TEMP_MESSAGE_TTL_SECONDS = 120

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["/status", "/demo"],
        ["/open", "/stats"],
        ["/history", "/panel"],
        ["/symbols", "/add", "/remove"],
        ["🥇 همه اطلاعات طلا"],
        ["/help", "/menu"],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="یک بخش را انتخاب کن...",
)


async def _delete_later(bot, chat_id: int, message_id: int) -> None:
    await asyncio.sleep(TEMP_MESSAGE_TTL_SECONDS)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _reply_temporary(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    if not update.effective_message:
        return
    message = await update.effective_message.reply_text(text, **kwargs)
    asyncio.create_task(_delete_later(context.bot, message.chat_id, message.message_id))


async def _authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    allowed_chat_id = context.application.bot_data.get("allowed_chat_id")
    actual_chat_id = update.effective_chat.id if update.effective_chat else None
    if not allowed_chat_id or str(actual_chat_id) != str(allowed_chat_id):
        if update.effective_message:
            await _reply_temporary(update, context, "دسترسی مجاز نیست.")
        return False
    return True


def _account_text() -> str:
    acct = runtime.account
    return (
        "💰 حساب دمو\n"
        f"موجودی اولیه: {acct.initial_balance:.2f} USDT\n"
        f"موجودی فعلی: {float(acct.balance or 0):.2f} USDT\n"
        "ریسک هر معامله: ۱٪ سرمایه بر اساس فاصله حدضرر\n"
        f"اهرم محاسباتی: {acct.leverage:g}x\n"
        f"مارجین مصرف‌شده: {acct.margin_used:.2f} USDT\n"
        f"مارجین آزاد: {acct.free_margin:.2f} USDT\n"
        f"معاملات باز: {len(acct.open_positions)}\n"
        "معاملات واقعی: خاموش"
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _authorized(update, context):
        await _reply_temporary(update, context, "🏠 منوی اصلی\nبخش موردنظر را انتخاب کن.", reply_markup=MAIN_MENU)


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    text = (update.effective_message.text or "").strip()
    if text == "🥇 همه اطلاعات طلا":
        await gold_status(update, context)
    elif text:
        # Legacy-friendly flow: sending a coin name as plain text adds it.
        await _add_symbol_requested(update, context, text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _authorized(update, context):
        await _reply_temporary(
            update, context,
            "ربات آماده است. بخش‌های فعال: استراتژی اصلی WEEX، حساب دمو و تحلیل طلای ۱۸ عیار.\n"
            "برای افزودن ارز فقط نام آن را بفرست، مثلاً BTC یا RENDER؛ فهرست: /symbols | حذف: /remove BTCUSDT\n"
            "ورودها مستقیم و فقط در حساب دمو هستند؛ معامله واقعی خاموش است.",
            reply_markup=MAIN_MENU,
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _authorized(update, context):
        await _reply_temporary(
            update, context,
            "وضعیت ربات: فعال\n"
            "تحلیل طلا: " + ("فعال" if runtime.gold_market is not None else "در انتظار کلید API") + "\n"
            "حساب دمو: فعال\n"
            f"اسکن WEEX: {runtime.scanner_state} | نمادهای دستی: {len(runtime.manual_symbols)}\n"
            "معاملات واقعی: خاموش",
        )


async def demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _authorized(update, context):
        await _reply_temporary(update, context, _account_text())


async def open_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    positions = runtime.account.open_positions
    if not positions:
        await _reply_temporary(update, context, "در حال حاضر معامله دمو بازی وجود ندارد.")
        return
    lines = ["📂 معاملات باز دمو:"]
    for p in positions:
        lines.append(
            f"{p.symbol} | {p.side.value}\n"
            f"ورود: {p.entry:g} | حد ضرر: {p.stop:g}\n"
            f"TP1: {p.tp1:g} | TP2: {p.tp2:g} | TP3: {p.tp3:g}"
        )
    await _reply_temporary(update, context, "\n\n".join(lines))


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    acct = runtime.account
    realized = sum(p.realized_pnl for p in acct.positions.values())
    closed = sum(1 for p in acct.positions.values() if p.closed)
    await _reply_temporary(
        update, context,
        f"📊 آمار حساب دمو\nموجودی: {float(acct.balance or 0):.2f} USDT\n"
        f"سود/زیان تحقق‌یافته: {realized:+.4f} USDT\n"
        f"معاملات بسته‌شده: {closed}\nمعاملات باز: {len(acct.open_positions)}",
    )


async def demo_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _authorized(update, context):
        await _reply_temporary(update, context, "📈 پنل حساب دمو\n\n" + _account_text())


async def trade_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    positions = sorted(runtime.account.positions.values(), key=lambda p: p.opened_at or "", reverse=True)
    if not positions:
        await _reply_temporary(update, context, "📜 هنوز معامله‌ای در حساب دمو ثبت نشده است.")
        return
    lines = ["📜 تاریخچه معاملات دمو"]
    for p in positions[:20]:
        state = "باز" if not p.closed else ("سود" if p.realized_pnl > 0 else "زیان" if p.realized_pnl < 0 else "سربه‌سر")
        lines.append(
            f"\n{p.symbol} | {p.side.value} | {state}\n"
            f"ورود: {p.entry:g} | حدضرر اولیه: {p.initial_stop:g}\n"
            f"TP1: {p.tp1:g} | TP2: {p.tp2:g} | TP3: {p.tp3:g}\n"
            f"سود/زیان تحقق‌یافته: {p.realized_pnl:+.4f} USDT\n"
            f"زمان ورود: {p.opened_at or 'نامشخص'}"
        )
    await _reply_temporary(update, context, "\n".join(lines))


async def list_symbols(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    symbols = runtime.manual_symbols
    text = "📋 نمادهای دستی برای اسکن:\n" + ("\n".join(symbols) if symbols else "هنوز نمادی اضافه نشده است.")
    await _reply_temporary(update, context, text)


async def _add_symbol_requested(update: Update, context: ContextTypes.DEFAULT_TYPE, requested: str) -> None:
    resolver = runtime.market_symbol_resolver
    if resolver is None:
        await _reply_temporary(update, context, "اتصال داده بازار هنوز آماده نیست؛ کمی بعد دوباره تلاش کن.")
        return
    try:
        market = await asyncio.to_thread(resolver.resolve_manual_symbol, requested)
    except Exception as exc:
        await _reply_temporary(update, context, f"نماد معتبر یا فعال در WEEX پیدا نشد: {exc}")
        return
    canonical = market.symbol
    if canonical in runtime.manual_symbols:
        await _reply_temporary(update, context, f"ℹ️ {canonical} از قبل در فهرست نمادهای دستی وجود دارد.")
        return
    runtime.manual_symbols.append(canonical)
    runtime.manual_symbols.sort()
    if runtime.storage is not None:
        runtime.storage.save_manual_symbols(runtime.manual_symbols)
    await _reply_temporary(update, context, f"✅ {canonical} به فهرست دستی اضافه شد. اسکن از این پس روی آن انجام می‌شود.")


async def add_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    if not context.args:
        await _reply_temporary(update, context, "فقط نام ارز را بفرست؛ مثلاً BTC یا RENDER. دستور /add هم همچنان فعال است.")
        return
    await _add_symbol_requested(update, context, context.args[0])


async def remove_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    if not context.args:
        await _reply_temporary(update, context, "نمونه: /remove BTCUSDT")
        return
    requested = context.args[0].strip().upper().replace("-", "/").replace(" ", "")
    resolver = runtime.market_symbol_resolver
    canonical = None
    if resolver is not None:
        try:
            market = await asyncio.to_thread(resolver.resolve_manual_symbol, requested)
            canonical = market.symbol
        except Exception:
            pass
    before = len(runtime.manual_symbols)
    runtime.manual_symbols = [symbol for symbol in runtime.manual_symbols if symbol.upper() != requested and (canonical is None or symbol != canonical)]
    if runtime.storage is not None:
        runtime.storage.save_manual_symbols(runtime.manual_symbols)
    if len(runtime.manual_symbols) == before:
        await _reply_temporary(update, context, "این نماد در فهرست دستی نبود.")
    else:
        await _reply_temporary(update, context, "نماد از فهرست اسکن حذف شد. سابقه و معاملات دمو پاک نشدند.")

async def gold_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    service = runtime.gold_market
    if service is None:
        await _reply_temporary(
            update, context,
            "🥇 تحلیل طلای ۱۸ عیار هنوز فعال نشده است.\n"
            "برای فعال‌سازی، کلید TALASANJ_API_KEY را در متغیرهای خصوصی HOKAS ثبت کن.\n"
            "کلید API را در چت ارسال نکن.",
        )
        return
    await _reply_temporary(update, context, service.status_text())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _authorized(update, context):
        await _reply_temporary(
            update, context,
            "/start - شروع\n/status - وضعیت ربات\n/gold - وضعیت تحلیل طلای ۱۸ عیار\n"
            "/demo - موجودی حساب دمو\n/open - معاملات باز دمو\n/stats - آمار دمو\n"
            "/panel - پنل حساب دمو\n/history - تاریخچه دمو\n"
            "فقط نام ارز را بفرست - افزودن نماد دستی (مثلاً BTC یا RENDER)\n/add نماد - افزودن با دستور\n/remove نماد - حذف نماد\n/symbols - فهرست نمادها\n"
            "/menu - منوی اصلی\n/help - راهنما",
            reply_markup=MAIN_MENU,
        )


def build_telegram_application(token: str, allowed_chat_id: str) -> Application:
    if not allowed_chat_id:
        raise ValueError("TELEGRAM_CHAT_ID is required to restrict bot access.")
    application = ApplicationBuilder().token(token).build()
    application.bot_data["allowed_chat_id"] = allowed_chat_id
    for command, callback in (
        ("start", start), ("menu", menu), ("status", status), ("gold", gold_status),
        ("demo", demo), ("open", open_positions), ("panel", demo_panel),
        ("history", trade_history), ("stats", stats), ("help", help_command),
        ("add", add_symbol), ("remove", remove_symbol), ("symbols", list_symbols),
    ):
        application.add_handler(CommandHandler(command, callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))
    return application
