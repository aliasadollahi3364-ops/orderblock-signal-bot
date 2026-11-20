"""Demo-only application entrypoint."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
import uvicorn
from telegram import Bot
from app.api import app
from app.charting import make_signal_chart
from app.config import Settings
from app.demo import DemoAccount
from app.explosive_scanner import ExplosiveAlert
from app.market_radar import RadarAlert
from app.market_data import WeexMarketData
from app.runtime import runtime
from app.scanner import AutomaticScanner, open_demo_from_signal
from app.storage import SQLiteStore
from app.strategy import Signal
from app.telegram_bot import build_telegram_application

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orderblock-signal-bot")

async def run_telegram(token: str, chat_id: str) -> None:
    if not token or not chat_id:
        logger.warning("Telegram disabled: configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        await asyncio.Event().wait()
        return
    application = build_telegram_application(token, chat_id)
    await application.initialize()
    await application.start()
    if application.updater is None:
        raise RuntimeError("Telegram updater unavailable.")
    await application.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

async def run_scanner(settings: Settings, loop: asyncio.AbstractEventLoop) -> None:
    market_data = WeexMarketData(settings.exchange_id, settings.quote_currency)
    runtime.market_symbol_resolver = market_data.resolve_manual_symbol
    bot = Bot(token=settings.telegram_bot_token) if settings.telegram_bot_token and settings.telegram_chat_id else None
    if bot is not None: await bot.initialize()
    def send(text: str) -> None:
        if bot is not None:
            future = asyncio.run_coroutine_threadsafe(bot.send_message(chat_id=settings.telegram_chat_id, text=text), loop)
            future.add_done_callback(lambda result: logger.error("Telegram notification failed: %s", result.exception()) if result.exception() else None)
    async def send_signal_photo(signal: Signal, position) -> None:
        if bot is None: return
        try:
            rows = await asyncio.to_thread(market_data.fetch_ohlcv, signal.symbol, "15m", 80)
            chart = make_signal_chart(rows, signal.symbol, signal.side, signal.entry, signal.stop, position.tp1, position.tp2, position.tp3)
            entry_time = datetime.fromtimestamp((signal.timestamp + 15*60*1000)/1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            method = "برخورد به اردربلاک ۴ساعته + تغییر روند و پولبک ۱۵دقیقه‌ای" if signal.method == "ORDER_BLOCK" else "شکست و پولبک خط روند ۱۵دقیقه‌ای"
            caption = (
                f"📣 سیگنال جدید دمو | {signal.symbol}\nروش ورود: {method}\n"
                "تایم‌فریم صدور سیگنال: ۱۵ دقیقه | روند و محدوده مرجع: اردربلاک ۴ ساعته\n"
                f"جهت: {signal.side}\nورود: {signal.entry:g}\nحد ضرر: {signal.stop:g}\n"
                f"TP1: {position.tp1:g}\nTP2: {position.tp2:g}\nTP3: {position.tp3:g}\n"
                f"زمان ورود: {entry_time}\n"
                "زمان خروج: پس از برخورد به حد ضرر یا اهداف، در پیام رویداد ثبت می‌شود.\n"
                "حالت: فقط دمو | معاملات واقعی: خاموش"
            )
            await bot.send_photo(chat_id=settings.telegram_chat_id, photo=chart, caption=caption)
        except Exception:
            logger.exception("Could not create/send signal chart for %s", signal.symbol)
            send(f"📣 سیگنال دمو {signal.symbol} | {signal.side}\nروش ورود: {signal.method}\nورود: {signal.entry:g}\nحد ضرر: {signal.stop:g}\nفقط دمو؛ معاملات واقعی خاموش")
    def handle_signal(signal: Signal) -> None:
        position = open_demo_from_signal(signal)
        if position is not None:
            runtime.closed_trade_events.append({
                "symbol": position.symbol,
                "side": position.side.value,
                "type": "OPEN",
                "price": position.entry,
                "time": position.opened_at,
                "realized_pnl": 0.0,
            })
            runtime.persist_demo_state()
            if bot is not None:
                asyncio.run_coroutine_threadsafe(send_signal_photo(signal, position), loop)
    def handle_explosive_alert(alert: ExplosiveAlert) -> None:
        stage = "🚨 هشدار اولیه حرکت انفجاری" if alert.stage == "EARLY" else "🔥 تأیید حرکت انفجاری"
        side = "لانگ" if alert.side == "BUY" else "شورت"
        reasons = "\n".join(f"• {reason}" for reason in alert.reasons)
        timestamp = datetime.fromtimestamp(alert.timestamp / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        send(
            f"{stage} | {alert.symbol}\nجهت: {side} | امتیاز: {alert.score}/100\n"
            f"ورود آزمایشی: {alert.entry:g}\nحدضرر: {alert.stop:g}\n"
            f"TP1: {alert.tp1:g}\nTP2: {alert.tp2:g}\nTP3: {alert.tp3:g}\n"
            f"زمان: {timestamp}\nدلایل:\n{reasons}\n"
            "این موتور مستقل و فقط هشداردهنده است؛ معامله دمو باز نمی‌کند."
        )

    def handle_radar_alert(alert: RadarAlert) -> None:
        category_names = {
            "EARLY": "پیش از پامپ / انباشت احتمالی",
            "BULLISH_BREAKOUT": "شروع حرکت صعودی",
            "ATH_WARNING": "هشدار نزدیک سقف تاریخیِ تاریخچه موجود",
            "BEARISH_BREAKDOWN": "شروع ریزش / شکست حمایت",
            "BOTTOM_REVERSAL": "برگشت احتمالی از کف تاریخچه موجود",
        }
        stage_names = {
            "WATCH": "👀 زیرنظر",
            "PRE_SIGNAL": "⚠️ نزدیک به تأیید",
            "CONFIRMED": "🚨 تأیید شرایط",
        }
        side = "صعودی" if alert.side == "BUY" else "نزولی"
        timestamp = datetime.fromtimestamp(alert.timestamp / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        reasons = "\n".join(f"• {reason}" for reason in alert.reasons)
        send(
            f"📡 رادار بازار | {category_names.get(alert.category, alert.category)}\n"
            f"{stage_names.get(alert.stage, alert.stage)} | جهت: {side} | امتیاز: {alert.score}/100\n"
            f"نماد: {alert.symbol}\nقیمت مرجع: {alert.reference_price:g}\n"
            f"زمان کندل: {timestamp}\n"
            + (
                f"\n📍 برنامه ورود رادار (فقط در سطح تأییدشده):\n"
                f"ورود: {alert.entry_price:g}\nحد ضرر ساختاری: {alert.stop_loss:g}\n"
                f"TP1 (2R): {alert.tp1:g}\nTP2 (3R): {alert.tp2:g}\nTP3 (4R): {alert.tp3:g}\n"
                if alert.stage == "CONFIRMED" and alert.entry_price is not None
                and alert.stop_loss is not None and alert.tp1 is not None
                and alert.tp2 is not None and alert.tp3 is not None else ""
            )
            + f"\nدلایل:\n{reasons}\n"
            + "هشدار تحلیلی مستقل است؛ دستور معامله یا سیگنال استراتژی اصلی نیست. فقط دمو."
        )

    def handle_trade_event(event: dict) -> None:
        """Keep TP/SL milestone notifications permanently in Telegram."""
        if bot is None:
            return
        event_labels = {
            "TP1": "🎯 هدف اول (TP1) زده شد",
            "TP2": "🎯 هدف دوم (TP2) زده شد",
            "TP3": "🏁 هدف سوم (TP3) زده شد؛ معامله کامل بسته شد",
            "STOP": "🛑 حد ضرر (SL) فعال شد؛ معامله بسته شد",
            "BREAK_EVEN_STOP": "⚖️ حد سربه‌سر فعال شد؛ معامله بسته شد",
            "TP1_TRAILING_STOP": "🔒 حد محافظتی بعد از TP2 فعال شد؛ معامله بسته شد",
        }
        label = event_labels.get(str(event.get("type")), f"رویداد معامله: {event.get('type')}")
        text = (
            f"{label} | {event['symbol']}\n"
            f"جهت: {event.get('side', 'نامشخص')}\n"
            f"قیمت رویداد: {event['price']:g}\n"
            f"زمان: {event['time']}\n"
            f"سود/زیان تحقق‌یافته تا این لحظه: {event['realized_pnl']:+.4f} USDT\n"
            "حساب دمو | معاملات واقعی: خاموش"
        )
        future = asyncio.run_coroutine_threadsafe(
            bot.send_message(chat_id=settings.telegram_chat_id, text=text), loop
        )
        future.add_done_callback(
            lambda result: logger.error("Could not send persistent trade event: %s", result.exception())
            if result.exception() else None
        )

    scanner = AutomaticScanner(market_data=market_data, strategy_config=runtime.strategy_config,
        universe_limit=settings.scanner_universe_limit, candidate_limit=settings.scanner_candidate_limit,
        on_signal=handle_signal, on_trade_event=handle_trade_event,
        on_explosive_alert=handle_explosive_alert, on_radar_alert=handle_radar_alert)
    try:
        await asyncio.to_thread(scanner.run_forever, settings.scan_interval_seconds)
    finally:
        market_data.close()
        if bot is not None: await bot.shutdown()

async def main() -> None:
    settings = Settings()
    settings.assert_demo_only()
    store = SQLiteStore(settings.database_path)
    runtime.account, runtime.closed_trade_events = store.load_state(DemoAccount(
        initial_balance=settings.demo_initial_balance, margin_per_trade=settings.demo_margin_per_trade,
        leverage=settings.demo_leverage))
    runtime.storage = store
    runtime.manual_symbols = store.load_manual_symbols()
    runtime.strategy_config = store.load_strategy_config()
    runtime.persist_demo_state()
    loop = asyncio.get_running_loop()
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info"))
    await asyncio.gather(server.serve(), run_telegram(settings.telegram_bot_token, settings.telegram_chat_id),
                         run_scanner(settings, loop))

if __name__ == "__main__":
    asyncio.run(main())
