"""Minimal demo + Iranian 18k gold application entrypoint."""
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
from app.gold_market import GoldMarketService, format_gold_signal
from app.market_data import WeexMarketData
from app.pending_limits import PendingLimit, PendingSetup, make_two_limit_prices
from app.scanner import AutomaticScanner, open_demo_from_signal
from app.runtime import runtime
from app.storage import SQLiteStore
from app.strategy import EarlyWarning, StrategyConfig
from app.telegram_bot import build_telegram_application

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orderblock-signal-bot")


async def telegram_request_with_retry(bot: Bot, method: str, **kwargs):
    """Initialize the Bot lazily and retry transient Telegram network failures."""
    delay = 1
    last_error = None
    for attempt in range(3):
        try:
            await bot.initialize()
            operation = getattr(bot, method)
            return await operation(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                logger.warning("Telegram %s failed (attempt %s/3): %s", method, attempt + 1, exc)
                await asyncio.sleep(delay)
                delay *= 2
    raise last_error


async def run_telegram(token: str, chat_id: str) -> None:
    if not token or not chat_id:
        logger.warning("Telegram disabled: configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        await asyncio.Event().wait()
        return
    delay = 2
    while True:
        application = build_telegram_application(token, chat_id)
        try:
            await application.initialize()
            await application.start()
            if application.updater is None:
                raise RuntimeError("Telegram updater unavailable.")
            await application.updater.start_polling()
            logger.info("Telegram polling started successfully.")
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram startup/polling failed; retrying in %s seconds.", delay)
        finally:
            try:
                if application.updater is not None and application.updater.running:
                    await application.updater.stop()
            except Exception:
                logger.exception("Telegram updater cleanup failed.")
            try:
                if application.running:
                    await application.stop()
            except Exception:
                logger.exception("Telegram application cleanup failed.")
            try:
                await application.shutdown()
            except Exception:
                logger.exception("Telegram shutdown cleanup failed.")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


async def run_gold_scanner(settings: Settings) -> None:
    """Independent Iranian 18k gold analysis; it never opens crypto or live positions."""
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
    bot_initialized = False
    logger.info("Gold analysis started; polling every %s seconds.", settings.gold_poll_seconds)
    try:
        while True:
            try:
                if bot is not None and not bot_initialized:
                    try:
                        await bot.initialize()
                        bot_initialized = True
                        logger.info("Gold Telegram notifications initialized.")
                    except Exception as exc:
                        logger.warning("Gold Telegram unavailable; analysis continues without notifications: %s", exc)
                service.update_strategy_config(runtime.strategy_config)
                signals = await asyncio.to_thread(service.poll_once)
                analysis = service.investment_analysis()
                for signal in signals:
                    if signal.side != "BUY" or analysis.get("score", 0) < 65:
                        continue
                    alert = (
                        "🚨 فرصت خرید طلای ۱۸ عیار\n"
                        f"امتیاز شرایط بازار: {analysis['score']}/100\n"
                        f"روش تأیید: {signal.method}\n"
                        + format_gold_signal(signal)
                    )
                    logger.info("Confirmed gold investment buy opportunity: %s", signal.method)
                    if bot is not None:
                        await telegram_request_with_retry(
                            bot, "send_message", chat_id=settings.telegram_chat_id, text=alert
                        )
                tehran_now = datetime.now(ZoneInfo("Asia/Tehran"))
                if bot is not None and tehran_now.hour >= settings.gold_daily_report_hour:
                    report_date = tehran_now.date().isoformat()
                    if not service.store.has_daily_report(report_date):
                        await telegram_request_with_retry(
                            bot, "send_message", chat_id=settings.telegram_chat_id,
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


async def run_crypto_scanner(settings: Settings) -> None:
    """Run the supplied Pine strategy on manually selected WEEX symbols, demo-only."""
    market_data = WeexMarketData(settings.exchange_id, settings.quote_currency)
    runtime.market_symbol_resolver = market_data
    loop = asyncio.get_running_loop()
    bot = Bot(token=settings.telegram_bot_token) if settings.telegram_bot_token and settings.telegram_chat_id else None
    if bot is not None:
        try:
            await bot.initialize()
        except Exception:
            logger.exception("Crypto Telegram notifications unavailable; demo scanning continues.")
            bot = None

    def notify(message: str) -> None:
        if bot is None:
            return
        future = asyncio.run_coroutine_threadsafe(
            bot.send_message(chat_id=settings.telegram_chat_id, text=message), loop
        )
        def report_error(done):
            try:
                done.result()
            except Exception:
                logger.exception("Crypto Telegram notification failed.")
        future.add_done_callback(report_error)

    async def send_pending_signal_photo(signal, near, deep, near_targets, deep_targets) -> None:
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
                "اهداف روی نمودار برای هر ورود تخمینی‌اند؛ پس از پرشدن سفارش‌ها، میانگین ورود و اهداف مشترک پوزیشن به‌روز می‌شوند.\n"
                "مارجین هر لیمیت: ۵ دلار | حداکثر مارجین کل: ۱۰ دلار. فقط حساب دمو."
            )
            await bot.send_photo(chat_id=settings.telegram_chat_id, photo=chart, caption=caption)
        except Exception:
            logger.exception("Could not create/send two-limit chart for %s", signal.symbol)

    async def send_signal_photo(signal, position) -> None:
        if bot is None:
            return
        try:
            rows = await asyncio.to_thread(market_data.fetch_ohlcv, signal.symbol, "15m", 80)
            chart = make_signal_chart(
                rows, signal.symbol, signal.side, signal.entry, signal.stop,
                position.tp1, position.tp2, position.tp3,
            )
            entry_time = datetime.fromtimestamp(
                (signal.timestamp + 15 * 60 * 1000) / 1000, timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC")
            rsi_label = {
                "BULLISH": "واگرایی صعودی هم‌جهت؛ تأیید اضافی",
                "BEARISH": "واگرایی نزولی هم‌جهت؛ تأیید اضافی",
                "OPPOSING_BEARISH": "واگرایی نزولی خلاف جهت؛ احتیاط",
                "OPPOSING_BULLISH": "واگرایی صعودی خلاف جهت؛ احتیاط",
                "NONE": "بدون واگرایی؛ سیگنال طبق شروط اصلی معتبر می‌ماند",
                "DISABLED": "بررسی واگرایی خاموش",
            }.get(signal.rsi_divergence, "نامشخص")
            caption = (
                f"📣 سیگنال جدید دمو | {signal.symbol}\n"
                f"جهت: {signal.side} | روش: {signal.method}\n"
                f"واگرایی RSI: {rsi_label}"
                + (f" | RSI: {signal.rsi_value:.1f}" if signal.rsi_value is not None else "")
                + "\n"
                f"ورود: {signal.entry:g}\nحد ضرر: {signal.stop:g}\n"
                f"TP1: {position.tp1:g} | TP2: {position.tp2:g} | TP3: {position.tp3:g}\n"
                f"زمان ورود: {entry_time}\n"
                "زمان خروج: پس از برخورد به حد ضرر یا اهداف، در پیام رویداد ثبت می‌شود.\n"
                "فقط حساب دمو؛ معاملات واقعی خاموش."
            )
            await bot.send_photo(chat_id=settings.telegram_chat_id, photo=chart, caption=caption)
        except Exception:
            logger.exception("Could not create/send signal chart for %s", signal.symbol)

    def on_early_warning(warning: EarlyWarning) -> None:
        # All standalone early-warning messages are disabled.
        # The confirmed two-limit alert is sent by on_signal and remains enabled.
        return

    def on_signal(signal) -> None:
        # Two staged limits are used only for the trendline pullback path.
        # Order-block entries remain direct Pine-strategy demo entries.
        if signal.method == "TRENDLINE":
            try:
                if signal.ob_top <= signal.ob_bottom or signal.volume_weighted_price is None:
                    raise ValueError("محدوده اردربلاک یا قیمت وزنی حجم برای سفارش لیمیت معتبر نیست.")
                near, deep = make_two_limit_prices(
                    signal.side, signal.ob_top, signal.ob_bottom,
                    signal.volume_weighted_price, reference_price=signal.entry,
                )
                created_at = signal.timestamp + 15 * 60 * 1000
                current_position = runtime.account.positions.get(signal.symbol)
                position_open = current_position is not None and not current_position.closed
                if not runtime.pending_limits.can_start_setup(
                    signal.symbol, position_open=position_open, created_at=created_at
                ):
                    logger.info("Two-limit setup suppressed for %s: position/setup already active.", signal.symbol)
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
                    direction = 1.0 if signal.side == "BUY" else -1.0
                    return (entry + direction * risk, entry + direction * 2 * risk,
                            entry + direction * 3 * risk)

                n1, n2, n3 = targets(near)
                d1, d2, d3 = targets(deep)
                confirmed_at = datetime.fromtimestamp(
                    created_at / 1000, timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S UTC")
                notify(
                    f"🚨 هشدار زودهنگام شکست خط روند + تأیید اردربلاک | {signal.symbol}\n"
                    f"جهت: {signal.side} | تایم‌فریم: ۱۵ دقیقه\n"
                    f"واگرایی RSI: {signal.rsi_divergence} | RSI: {signal.rsi_value}\n"
                    f"لیمیت ۱ (نزدیک‌تر): {near:.8g} | TP1: {n1:.8g} | TP2: {n2:.8g} | TP3: {n3:.8g}\n"
                    f"لیمیت ۲ (عمیق‌تر): {deep:.8g} | TP1: {d1:.8g} | TP2: {d2:.8g} | TP3: {d3:.8g}\n"
                    f"حد ضرر ساختاری: {signal.stop:.8g}\n"
                    "مارجین هر لیمیت: ۵ دلار | در صورت پرشدن هر دو: ۱۰ دلار مارجین\n"
                    f"زمان تأیید: {confirmed_at}\n"
                    "وضعیت: منتظر پولبک و لمس هر لیمیت؛ تا قبل از لمس، معامله‌ای در حساب دمو باز نیست.\n"
                    "قیمت وزنی از OHLCV و حجم کندل‌ها تخمین زده شده؛ دفتر سفارش یا Volume Profile واقعی نیست.\n"
                    "حساب دمو | معاملات واقعی: خاموش"
                )
                if bot is not None:
                    asyncio.run_coroutine_threadsafe(
                        send_pending_signal_photo(signal, near, deep, (n1, n2, n3), (d1, d2, d3)),
                        loop,
                    )
            except Exception as exc:
                logger.warning("Two-limit setup rejected for %s: %s", signal.symbol, exc)
            return

        position = open_demo_from_signal(signal)
        if position is None:
            return
        notify(
            f"📣 سیگنال دمو {signal.side} | {signal.symbol}\n"
            f"روش: {signal.method}\n"
            f"ورود: {position.entry:.8g}\n"
            f"حد ضرر اولیه: {position.stop:.8g}\n"
            f"TP1 (1R): {position.tp1:.8g} | خروج ۳۳٪\n"
            f"TP2 (2R): {position.tp2:.8g} | خروج ۳۳٪\n"
            f"TP3 ({signal.risk_reward:g}R): {position.tp3:.8g} | خروج باقی‌مانده\n"
            f"زمان: {position.opened_at}\n"
            f"واگرایی RSI: {signal.rsi_divergence} | RSI: {signal.rsi_value}\n"
            "پس از TP1 حد ضرر به ورود و پس از TP2 به TP1 منتقل می‌شود. فقط حساب دمو؛ بدون سفارش واقعی."
        )
        if bot is not None:
            asyncio.run_coroutine_threadsafe(send_signal_photo(signal, position), loop)

    def on_pending_event(event: dict) -> None:
        event_type = event.get("type", "LIMIT_UPDATE")
        leg = event.get("leg", "?")
        price = event.get("price", "")
        if event_type == "LIMIT_FILLED":
            message = (
                f"✅ سفارش لیمیت {leg} پر شد | {event.get('symbol', '')}\n"
                f"قیمت اجرا: {price}\n"
                "ورود در حساب دمو ثبت شد؛ سفارش دوم مستقل باقی می‌ماند."
            )
        else:
            message = (
                f"⚪ سفارش لیمیت {leg} لغو شد | {event.get('symbol', '')}\n"
                f"قیمت سفارش: {price}\n"
                f"علت: {event.get('reason', 'لغو/انقضا')}"
            )
        notify(message)

    def on_trade_event(event: dict) -> None:
        kind = event.get("type", "UPDATE")
        price = event.get("price", "")
        notify(
            f"📊 نتیجه معامله دمو {event.get('symbol', '')}\n"
            f"وضعیت: {kind}\nقیمت خروج: {price}\n"
            f"سود/زیان: {event.get('realized_pnl', 0):+.4f} USDT"
        )

    scanner = AutomaticScanner(
        market_data=market_data,
        strategy_config=runtime.strategy_config,
        universe_limit=settings.scanner_universe_limit,
        candidate_limit=settings.scanner_candidate_limit,
        on_signal=on_signal,
        on_early_warning=on_early_warning,
        on_trade_event=on_trade_event,
        on_pending_event=on_pending_event,
    )
    logger.info("Pine strategy scanner started; manual symbols only; demo-only.")
    try:
        await asyncio.to_thread(scanner.run_forever, settings.scan_interval_seconds)
    finally:
        market_data.close()
        if bot is not None:
            await bot.shutdown()


async def main() -> None:
    settings = Settings()
    settings.assert_demo_only()
    store = SQLiteStore(settings.database_path)
    runtime.account, runtime.closed_trade_events = store.load_state(DemoAccount(
        initial_balance=settings.demo_initial_balance,
        margin_per_trade=settings.demo_margin_per_trade,
        leverage=settings.demo_leverage,
    ))
    runtime.storage = store
    runtime.pending_limits = store.load_pending_setups()
    # Keep the user's manually selected symbols and the existing demo ledger.
    runtime.manual_symbols = store.load_manual_symbols()
    runtime.selected_symbols = []
    runtime.signal_count = 0
    runtime.scanner_state = "WAITING_FOR_MANUAL_SYMBOLS"
    # Restore the user's saved Telegram strategy settings; fall back to Pine defaults only on first setup.
    runtime.strategy_config = store.load_strategy_config()
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info"))
    await asyncio.gather(
        server.serve(),
        run_telegram(settings.telegram_bot_token, settings.telegram_chat_id),
        run_gold_scanner(settings),
        run_crypto_scanner(settings),
    )


if __name__ == "__main__":
    asyncio.run(main())
