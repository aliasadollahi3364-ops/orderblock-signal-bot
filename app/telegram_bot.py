"""Telegram interface for the preserved demo account and Iranian 18k gold module."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from app.runtime import runtime
from app.liquidity import LiquidityConfig
from app.strategy_pine_v6 import StrategyConfig

logger = logging.getLogger(__name__)
TEMP_MESSAGE_TTL_SECONDS = 120

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["/status", "/demo"],
        ["/open", "/stats"],
        ["/history", "/panel"],
        ["/symbols", "/add", "/remove"],
        ["/liquidity"],
        ["⚙️ تنظیمات کلی استراتژی"],
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
    elif text == "⚙️ تنظیمات کلی استراتژی":
        await strategy_settings(update, context)
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
            f"اسکن جمع‌آوری نقدینگی: {runtime.liquidity_scanner_state} | تایم‌فریم: ۱۵ دقیقه\n"
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


def _liquidity_status_text() -> str:
    account = runtime.liquidity_account
    config = runtime.liquidity_config or LiquidityConfig()
    state = "فعال" if runtime.liquidity_enabled else "خاموش"
    if account is None:
        return (
            "💧 استراتژی جمع‌آوری نقدینگی\\n"
            f"وضعیت اسکنر: {state} | تایم‌فریم: ۱۵ دقیقه\\n"
            "حساب دمو در حال آماده‌سازی است."
        )
    lines = [
        "💧 استراتژی جمع‌آوری نقدینگی",
        f"وضعیت اسکنر: {state} | تایم‌فریم: ۱۵ دقیقه",
        f"EMA {config.ema_length}: {'فیلتر روشن' if config.use_ema_filter else 'فیلتر خاموش'}",
        f"فیلتر سشن: {'روشن' if config.use_session_filter else 'خاموش'}",
        "",
        "💰 حساب دمو مستقل",
        f"موجودی اولیه: {account.initial_balance:.2f} USDT",
        f"موجودی فعلی: {float(account.balance or 0):.2f} USDT",
        f"سود/زیان تحقق‌یافته: {account.realized_pnl:+.4f} USDT",
        "مارجین هر معامله: 5 USDT | اهرم: 5x | ارزش اسمی: 25 USDT",
        f"مارجین مصرف‌شده: {account.margin_used:.2f} USDT",
        f"مارجین آزاد: {account.free_margin:.2f} USDT",
        f"معاملات باز: {len(account.open_positions)}",
    ]
    for position in account.open_positions:
        lines.append(
            f"{position.symbol} | {position.side} | ورود: {position.entry:g} | "
            f"SL: {position.stop:g} | TP: {position.target:g}"
        )
    lines += [
        "",
        "دستورات:",
        "/liquidity on یا /liquidity off",
        "/liquidity ema on یا /liquidity ema off",
        "/liquidity session on یا /liquidity session off",
        "فقط تایم‌فریم ۱۵ دقیقه؛ معاملات واقعی خاموش.",
    ]
    return "\\n".join(lines)


async def liquidity_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    args = [arg.strip().lower() for arg in context.args]
    config = runtime.liquidity_config or LiquidityConfig()
    try:
        if not args:
            await _reply_temporary(update, context, _liquidity_status_text())
            return
        if len(args) == 1 and args[0] in {"on", "off", "روشن", "خاموش"}:
            runtime.liquidity_enabled = args[0] in {"on", "روشن"}
            message = "اسکنر جمع‌آوری نقدینگی " + ("روشن شد." if runtime.liquidity_enabled else "خاموش شد.")
        elif len(args) == 2 and args[0] in {"ema", "session"} and args[1] in {"on", "off", "روشن", "خاموش"}:
            value = args[1] in {"on", "روشن"}
            if args[0] == "ema":
                config = replace(config, use_ema_filter=value)
                label = "فیلتر EMA"
            else:
                config = replace(config, use_session_filter=value)
                label = "فیلتر سشن"
            runtime.liquidity_config = config
            message = f"{label} " + ("روشن شد." if value else "خاموش شد.")
        else:
            raise ValueError("نمونه: /liquidity on یا /liquidity ema off یا /liquidity session on")
        if runtime.liquidity_store is not None and runtime.liquidity_account is not None:
            runtime.liquidity_store.save(
                runtime.liquidity_account, runtime.liquidity_config or config,
                runtime.liquidity_enabled, runtime.liquidity_cursors,
            )
        await _reply_temporary(update, context, "✅ " + message + "\\n\\n" + _liquidity_status_text())
    except (TypeError, ValueError) as exc:
        await _reply_temporary(update, context, f"تنظیم انجام نشد: {exc}")


STRATEGY_BOOL_SETTINGS = {
    "use_trend_filter": "هم‌جهتی روند ۴ ساعته برای OB",
    "require_ob_touch": "الزام لمس اولیه OB",
    "use_touch_freshness": "تازگی لمس اولیه OB",
    "use_choch_strength": "قدرت کندل تغییر ساختار",
    "use_confirm_strength": "قدرت کندل تأیید",
    "enable_trendline_signals": "مسیر مستقل خط روند",
    "use_sma200_trendline_filter": "فیلتر SMA200 خط روند",
    "use_sma200_filter": "فیلتر SMA200 اردربلاک",
    "enable_rsi_divergence": "نمایش واگرایی RSI (غیرفیلتر ورود)",
    "use_ob_boundary_sl": "حدضرر مرز اردربلاک",
}
STRATEGY_NUMBER_SETTINGS = {
    "min_ob_score": ("حداقل امتیاز OB", 0, 100),
    "risk_percent": ("ریسک هر معامله درصد", 0.1, 5.0),
    "risk_reward": ("ضریب هدف نهایی R", 0.5, 10.0),
    "sl_buffer_atr": ("ضریب فاصله حدضرر ATR", 0.0, 2.0),
    "max_bars_since_touch": ("حداکثر کندل از لمس اولیه OB", 1, 200),
    "trendline_pullback_max_wait": ("مهلت پولبک خط روند", 1, 100),
    "min_choch_body_atr": ("حداقل قدرت تغییر ساختار برحسب ATR", 0.0, 2.0),
    "min_confirm_body_atr": ("حداقل قدرت کندل تأیید برحسب ATR", 0.0, 2.0),
    "retest_atr": ("تلورانس پولبک به OB برحسب ATR", 0.0, 2.0),
    "trendline_touch_tolerance_atr": ("تلورانس پیوت سوم خط روند برحسب ATR", 0.05, 1.5),
    "trendline_retest_atr": ("تلورانس پولبک خط روند برحسب ATR", 0.0, 2.0),
}

def _strategy_settings_text() -> str:
    cfg = runtime.strategy_config
    lines = ["⚙️ تنظیمات کلی استراتژی", "وضعیت فعلی:"]
    for key, label in STRATEGY_BOOL_SETTINGS.items():
        lines.append(f"{label}: {'روشن' if getattr(cfg, key) else 'خاموش'}")
    lines += [
        f"ریسک هر معامله: {cfg.risk_percent:g}٪",
        f"هدف نهایی: {cfg.risk_reward:g}R (یک هدف، مطابق Pine)",
        f"فاصله حدضرر ATR: {cfg.sl_buffer_atr:g}",
        f"حداکثر کندل از لمس OB: {cfg.max_bars_since_touch}",
        f"مهلت پولبک خط روند: {cfg.trendline_pullback_max_wait}",
        f"حداقل امتیاز اعتبار OB: {cfg.min_ob_score}/100",
        "مسیر OB: تغییر ساختار + پولبک به خود محدوده OB + کندل تأیید (اجباری)",
        "مسیر خط روند: ۳ پیوت معتبر + شکست + پولبک + کندل تأیید؛ مستقل از OB",
        "شکار نقدینگی: عبور سایه از مرز OB و بسته‌شدن دوباره داخل محدوده؛ سپس تأیید کندل",
        f"قدرت CHoCH: {cfg.min_choch_body_atr:g} ATR",
        f"قدرت کندل تأیید: {cfg.min_confirm_body_atr:g} ATR",
        "",
        "تغییر روشن/خاموش:",
        "/strategy enable_trendline_signals on یا off",
        "تغییر عددی:",
        "/strategy risk_percent 1.0",
        "نام گزینه‌ها را دقیقاً مانند نمونه وارد کن.",
        "تنظیمات در SQLite ذخیره می‌شوند و بعد از راه‌اندازی مجدد باقی می‌مانند.",
        "ورود مستقیم با یک هدف نهایی مطابق Pine؛ حساب دمو و معاملات واقعی خاموش است.",
    ]
    return "\n".join(lines)

async def strategy_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    if not context.args:
        await _reply_temporary(update, context, _strategy_settings_text())
        return
    if len(context.args) != 2:
        await _reply_temporary(update, context, "نمونه: /strategy enable_trendline_signals on یا /strategy min_ob_score 70")
        return
    key, raw_value = context.args[0].strip(), context.args[1].strip().lower()
    cfg = runtime.strategy_config or StrategyConfig()
    try:
        if key in STRATEGY_BOOL_SETTINGS:
            if raw_value in {"on", "true", "1", "روشن"}:
                value = True
            elif raw_value in {"off", "false", "0", "خاموش"}:
                value = False
            else:
                raise ValueError("مقدار باید on یا off باشد.")
        elif key in STRATEGY_NUMBER_SETTINGS:
            label, minimum, maximum = STRATEGY_NUMBER_SETTINGS[key]
            value = float(raw_value)
            if not minimum <= value <= maximum:
                raise ValueError(f"مقدار {key} باید بین {minimum} و {maximum} باشد.")
            if key in {"max_bars_since_touch", "trendline_pullback_max_wait", "min_ob_score"}:
                if not value.is_integer():
                    raise ValueError(f"{key} باید عدد صحیح باشد.")
                value = int(value)
        else:
            raise ValueError("این گزینه قابل تغییر نیست یا شناخته نشد.")
        updated = replace(cfg, **{key: value})
        # These two execution constraints are intentionally immutable.
        updated.require_trendline_pullback = True
        updated.use_trailing_stop = False
        runtime.strategy_config = updated
        if runtime.storage is not None:
            runtime.storage.save_strategy_config(updated)
        await _reply_temporary(
            update, context,
            f"✅ تنظیم شد: {STRATEGY_BOOL_SETTINGS.get(key, STRATEGY_NUMBER_SETTINGS.get(key, (key,))[0])} = "
            f"{('روشن' if value else 'خاموش') if isinstance(value, bool) else value}\n"
            "از چرخه اسکن بعدی اعمال می‌شود.\n\n" + _strategy_settings_text(),
        )
    except (TypeError, ValueError) as exc:
        await _reply_temporary(update, context, f"تنظیم انجام نشد: {exc}")


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
            "/strategy - تنظیمات کلی استراتژی\n"
            "/strategy نام_گزینه on|off|عدد - تغییر تنظیم\n"
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
        ("strategy", strategy_settings), ("liquidity", liquidity_settings),
    ):
        application.add_handler(CommandHandler(command, callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))
    return application
