"""Demo-only application entrypoint."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import uvicorn
from telegram import Bot
from app.api import app
from app.charting import make_signal_chart
from app.config import Settings
from app.demo import DemoAccount
from app.market_data import WeexMarketData
from app.pending_limits import PendingLimit, PendingSetup, make_two_limit_prices
from app.gold_market import GoldMarketService, format_gold_signal
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
            rsi_caption = {
                "BULLISH": "واگرایی صعودی هم‌جهت؛ تأیید اضافی",
                "BEARISH": "واگرایی نزولی هم‌جهت؛ تأیید اضافی",
                "OPPOSING_BEARISH": "واگرایی نزولی خلاف جهت؛ احتیاط",
                "OPPOSING_BULLISH": "واگرایی صعودی خلاف جهت؛ احتیاط",
                "NONE": "بدون واگرایی؛ سیگنال طبق شروط اصلی معتبر می‌ماند",
                "DISABLED": "بررسی واگرایی خاموش",
            }.get(signal.rsi_divergence, "نامشخص")
            caption = (
                f"📣 سیگنال جدید دمو | {signal.symbol}\nروش ورود: {method}\n"
                "تایم‌فریم صدور سیگنال: ۱۵ دقیقه | روند و محدوده مرجع: اردربلاک ۴ ساعته\n"
                f"جهت: {signal.side}\n"
                f"واگرایی RSI: {rsi_caption}"
                + (f" | RSI: {signal.rsi_value:.1f}" if signal.rsi_value is not None else "")
                + "\n"
                f"ورود: {signal.entry:g}\nحد ضرر: {signal.stop:g}\n"
                f"TP1: {position.tp1:g}\nTP2: {position.tp2:g}\nTP3: {position.tp3:g}\n"
                f"زمان ورود: {entry_time}\n"
                "زمان خروج: پس از برخورد به حد ضرر یا اهداف، در پیام رویداد ثبت می‌شود.\n"
                "حالت: فقط دمو | معاملات واقعی: خاموش"
            )
            await bot.send_photo(chat_id=settings.telegram_chat_id, photo=chart, caption=caption)
        except Exception:
            logger.exception("Could not create/send signal chart for %s", signal.symbol)
            send(f"📣 سیگنال دمو {signal.symbol} | {signal.side}\nروش ورود: {signal.method}\nورود: {signal.entry:g}\nحد ضرر: {signal.stop:g}\nفقط دمو؛ معاملات واقعی خاموش")
    async def send_pending_signal_photo(signal: Signal, near: float, deep: float,
                                         near_targets: tuple[float, float, float],
                                         deep_targets: tuple[float, float, float]) -> None:
        if bot is None:
            return
        try:
            rows = await asyncio.to_thread(market_data.fetch_ohlcv, signal.symbol, "15m", 80)
            chart = make_signal_chart(
                rows, signal.symbol, signal.side, near, signal.stop,
                near_targets[0], near_targets[1], near_targets[2],
                entry2=deep, tp1_2=deep_targets[0],
                tp2_2=deep_targets[1], tp3_2=deep_targets[2],
            )
            caption = (
                f"📊 شکست خط روند + اردربلاک | {signal.symbol}\n"
                f"لیمیت ۱: {near:g} | لیمیت ۲: {deep:g}\n"
                f"حد ضرر: {signal.stop:g}\n"
                "اهداف روی نمودار برای هر ورود تخمینی‌اند؛ پس از پرشدن سفارش‌ها، میانگین ورود و اهداف مشترک پوزیشن به‌روز می‌شوند.\\n"
                "مارجین هر لیمیت: ۵ دلار | حداکثر مارجین کل: ۱۰ دلار. فقط حساب دمو."
            )
            await bot.send_photo(chat_id=settings.telegram_chat_id, photo=chart, caption=caption)
        except Exception:
            logger.exception("Could not create/send two-limit chart for %s", signal.symbol)

    def handle_signal(signal: Signal) -> None:
        if signal.method == "TRENDLINE":
            # The trendline path alerts immediately, then waits for either of
            # the two simulated limit prices to be touched on a later 15m bar.
            try:
                if signal.ob_top <= signal.ob_bottom or signal.volume_weighted_price is None:
                    raise ValueError("سیگنال خط روند محدوده اردربلاک معتبر یا قیمت وزنی حجم ندارد.")
                near, deep = make_two_limit_prices(
                    signal.side, signal.ob_top, signal.ob_bottom,
                    signal.volume_weighted_price,
                )
                created_at = signal.timestamp + 15 * 60 * 1000
                existing = runtime.pending_limits.setups.get(signal.symbol)
                if existing is not None and existing.active:
                    logger.info("Pending setup already active for %s; duplicate alert suppressed.", signal.symbol)
                    return
                if existing is not None and existing.created_at == created_at:
                    logger.info("Replayed trendline signal suppressed for %s.", signal.symbol)
                    return
                setup = PendingSetup(
                    symbol=signal.symbol,
                    side=signal.side,
                    created_at=created_at,
                    expires_after_bars=runtime.strategy_config.trendline_pullback_max_wait,
                    invalidation=signal.stop,
                    stop=signal.stop,
                    ob_top=signal.ob_top,
                    ob_bottom=signal.ob_bottom,
                    limits=[PendingLimit(1, near), PendingLimit(2, deep)],
                )
                runtime.pending_limits.add(setup)
                runtime.persist_pending_limits()
                def targets(entry: float) -> tuple[float, float, float]:
                    risk = abs(entry - signal.stop)
                    sign = 1.0 if signal.side == "BUY" else -1.0
                    return (entry + sign * risk, entry + sign * 2 * risk, entry + sign * 3 * risk)
                t1, t2, t3 = targets(near)
                u1, u2, u3 = targets(deep)
                entry_time = datetime.fromtimestamp(created_at / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                rsi_label = {
                    "BULLISH": "واگرایی هم‌جهت صعودی؛ تأیید RSI",
                    "BEARISH": "واگرایی هم‌جهت نزولی؛ تأیید RSI",
                    "OPPOSING_BEARISH": "واگرایی نزولی خلاف جهت خرید؛ احتیاط",
                    "OPPOSING_BULLISH": "واگرایی صعودی خلاف جهت فروش؛ احتیاط",
                    "NONE": "واگرایی معتبر مشاهده نشد؛ سیگنال طبق شروط اصلی مجاز است",
                    "DISABLED": "فیلتر واگرایی RSI خاموش است",
                }.get(signal.rsi_divergence, "وضعیت واگرایی RSI نامشخص")
                alert = (
                    f"🚨 هشدار زودهنگام شکست خط روند + تأیید اردربلاک | {signal.symbol}\n"
                    f"جهت: {signal.side} | تایم‌فریم: ۱۵ دقیقه\n"
                    f"واگرایی RSI: {rsi_label}"
                    + (f" | RSI: {signal.rsi_value:.1f}" if signal.rsi_value is not None else "")
                    + "\n"
                    f"لیمیت ۱ (نزدیک‌تر): {near:g} | TP1: {t1:g} | TP2: {t2:g} | TP3: {t3:g}\n"
                    f"لیمیت ۲ (عمیق‌تر): {deep:g} | TP1: {u1:g} | TP2: {u2:g} | TP3: {u3:g}\n"
                    f"حد ضرر ساختاری: {signal.stop:g}\n"
                    f"زمان تأیید: {entry_time}\n"
                    "وضعیت: منتظر پولبک و لمس هر لیمیت؛ تا قبل از لمس، معامله‌ای در حساب دمو باز نیست.\n"
                    "قیمت وزنی از OHLCV و حجم کندل‌ها تخمین زده شده؛ دفتر سفارش یا Volume Profile واقعی نیست.\n"
                    "حساب دمو | معاملات واقعی: خاموش"
                )
                send(alert)
                if bot is not None:
                    asyncio.run_coroutine_threadsafe(
                        send_pending_signal_photo(signal, near, deep,
                                                  (t1, t2, t3), (u1, u2, u3)),
                        loop,
                    )
            except (ValueError, TypeError) as exc:
                logger.warning("Trendline pending setup rejected for %s: %s", signal.symbol, exc)
                send(f"⚠️ سیگنال خط روند {signal.symbol} ایجاد نشد: {exc}")
            return

        # Preserve the existing order-block-only demo entry flow unchanged.
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
        if event.get("leg") is not None:
            label += f" | لیمیت شماره {event['leg']}"
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

    def handle_pending_event(event: dict) -> None:
        if event.get("type") == "LIMIT_FILLED":
            message = (
                f"✅ لیمیت شماره {event['leg']} پر شد | {event['symbol']}\\n"
                f"جهت: {event['side']}\\nقیمت پرشدن این لیمیت: {event['price']:g}\\n"
                f"میانگین ورود پوزیشن واحد: {event['average_entry']:g}\\n"
                f"مارجین استفاده‌شده: {event['margin_allocated']:g} دلار\\n"
                f"اهداف مشترک: TP1 {event['tp1']:g} | TP2 {event['tp2']:g} | TP3 {event['tp3']:g}\\n"
                f"زمان: {event['time']}\\nحساب دمو؛ معاملات واقعی خاموش"
            )
        else:
            message = (
                f"❌ لیمیت شماره {event['leg']} لغو شد | {event['symbol']}\n"
                f"جهت: {event['side']}\nقیمت سفارش: {event['price']:g}\n"
                f"زمان: {event['time']}\nعلت لغو: {event.get('reason', 'نامشخص')}\n"
                "حساب دمو؛ معاملات واقعی خاموش"
            )
        send(message)

    scanner = AutomaticScanner(market_data=market_data, strategy_config=runtime.strategy_config,
        universe_limit=settings.scanner_universe_limit, candidate_limit=settings.scanner_candidate_limit,
        on_signal=handle_signal, on_trade_event=handle_trade_event,
        on_pending_event=handle_pending_event,
        )
    try:
        await asyncio.to_thread(scanner.run_forever, settings.scan_interval_seconds)
    finally:
        market_data.close()
        if bot is not None: await bot.shutdown()

async def run_gold_scanner(settings: Settings) -> None:
    """Independent gold investment analysis; it never opens crypto demo or live positions."""
    if not settings.talasanj_api_key:
        logger.info("Gold analysis disabled: configure TALASANJ_API_KEY in private HOKAS environment.")
        return
    service = GoldMarketService(
        api_key=settings.talasanj_api_key,
        database_path=settings.database_path,
        strategy_config=runtime.strategy_config,
        client_name=settings.talasanj_client_name,
        client_url=settings.talasanj_client_url,
    )
    runtime.gold_market = service
    bot = Bot(token=settings.telegram_bot_token) if settings.telegram_bot_token and settings.telegram_chat_id else None
    if bot is not None:
        await bot.initialize()
    logger.info("Gold analysis started; polling every %s seconds.", settings.gold_poll_seconds)
    try:
        while True:
            try:
                service.update_strategy_config(runtime.strategy_config)
                signals = await asyncio.to_thread(service.poll_once)
                analysis = service.investment_analysis()
                for signal in signals:
                    # For a 3-6 month investment horizon, only confirmed BUY setups
                    # from the user's existing 4H/15M strategy can trigger an entry alert.
                    if signal.side != "BUY" or analysis.get("score", 0) < 65:
                        continue
                    alert = (
                        "🚨 فرصت خرید طلا؛ استراتژی اصلی تأیید کرد\n"
                        f"امتیاز شرایط بازار: {analysis['score']}/100\n"
                        f"روش تأیید: {signal.method} (ساختار ۴ساعته + ورود ۱۵دقیقه‌ای)\n"
                        + format_gold_signal(signal)
                    )
                    logger.info("Confirmed gold investment buy opportunity: %s", signal.method)
                    if bot is not None:
                        await bot.send_message(chat_id=settings.telegram_chat_id, text=alert)
                tehran_now = datetime.now(ZoneInfo("Asia/Tehran"))
                if bot is not None and tehran_now.hour >= settings.gold_daily_report_hour:
                    report_date = tehran_now.date().isoformat()
                    if not service.store.has_daily_report(report_date):
                        await bot.send_message(
                            chat_id=settings.telegram_chat_id,
                            text=service.investment_report(),
                        )
                        service.store.claim_daily_report(report_date)
            except Exception as exc:
                service.last_error = str(exc)[:400]
                logger.exception("Gold investment analysis failed")
            await asyncio.sleep(settings.gold_poll_seconds)
    finally:
        if bot is not None:
            await bot.shutdown()


async def main() -> None:
    settings = Settings()
    settings.assert_demo_only()
    store = SQLiteStore(settings.database_path)
    runtime.account, runtime.closed_trade_events = store.load_state(DemoAccount(
        initial_balance=settings.demo_initial_balance, margin_per_trade=settings.demo_margin_per_trade,
        leverage=settings.demo_leverage))
    runtime.storage = store
    runtime.pending_limits = store.load_pending_setups()
    runtime.manual_symbols = store.load_manual_symbols()
    runtime.strategy_config = store.load_strategy_config()
    runtime.persist_demo_state()
    loop = asyncio.get_running_loop()
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info"))
    await asyncio.gather(server.serve(), run_telegram(settings.telegram_bot_token, settings.telegram_chat_id),
                         run_scanner(settings, loop), run_gold_scanner(settings))

if __name__ == "__main__":
    asyncio.run(main())
