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
from app.liquidity import LiquidityScanner, LiquidityStore
from app.scanner import AutomaticScanner, open_demo_from_signal
from app.runtime import runtime
from app.storage import SQLiteStore
from app.strategy_pine_v6 import EarlyWarning, StrategyConfig
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

    async def send_signal_photo(signal, position=None) -> None:
        if bot is None:
            return
        try:
            rows = await asyncio.to_thread(market_data.fetch_ohlcv, signal.symbol, "15m", 80)
            entry = position.entry if position is not None else signal.entry
            stop = position.stop if position is not None else signal.stop
            direction = 1 if signal.side == "BUY" else -1
            risk_distance = abs(entry - stop)
            first_r = 1.0 if signal.risk_reward >= 2.0 else signal.risk_reward / 3.0
            second_r = 2.0 if signal.risk_reward >= 2.0 else signal.risk_reward * 2.0 / 3.0
            tp1 = position.tp1 if position is not None else entry + direction * risk_distance * first_r
            tp2 = position.tp2 if position is not None else entry + direction * risk_distance * second_r
            tp3 = position.tp3 if position is not None else entry + direction * risk_distance * signal.risk_reward
            chart = make_signal_chart(
                rows, signal.symbol, signal.side, entry, stop, tp1, tp2, tp3,
            )
            entry_time = datetime.fromtimestamp(
                (signal.timestamp + 15 * 60 * 1000) / 1000, timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC")
            rsi_label = {
                "BULLISH": "واگرایی صعودی؛ نمایش کمکی",
                "BEARISH": "واگرایی نزولی؛ نمایش کمکی",
                "OPPOSING_BEARISH": "واگرایی نزولی خلاف جهت؛ نمایش کمکی",
                "OPPOSING_BULLISH": "واگرایی صعودی خلاف جهت؛ نمایش کمکی",
                "NONE": "واگرایی RSI مشاهده نشد",
                "DISABLED": "نمایش واگرایی خاموش",
            }.get(signal.rsi_divergence, "نامشخص")
            caption = (
                f"📣 سیگنال استراتژی | {signal.symbol}\n"
                f"جهت: {signal.side} | روش: {signal.method}\n"
                f"واگرایی RSI: {rsi_label}"
                + (f" | RSI: {signal.rsi_value:.1f}" if signal.rsi_value is not None else "")
                + "\n"
                f"ورود: {entry:g}\nحد ضرر: {stop:g}\n"
                f"TP1: {tp1:g} | TP2: {tp2:g} | TP3: {tp3:g}\n"
                + (f"امتیاز اعتبار OB: {signal.ob_score:g}/100\n" if signal.ob_score is not None else "")
                + f"زمان سیگنال: {entry_time}\n"
                + ("ثبت در حساب دمو انجام شد." if position is not None else
                   "سیگنال ارسال شد؛ ثبت معامله دمو به دلیل محدودیت حساب انجام نشد.")
                + "\nفقط حساب دمو؛ معاملات واقعی خاموش."
            )
            await bot.send_photo(chat_id=settings.telegram_chat_id, photo=chart, caption=caption)
        except Exception:
            logger.exception("Could not create/send signal chart for %s", signal.symbol)

    def on_early_warning(warning: EarlyWarning) -> None:
        # Only notify after a 15m trendline retest candle has actually touched
        # an active, direction-aligned order block and confirmed the direction.
        if warning.kind != "TRENDLINE_BREAK_OB_TOUCH":
            return
        entry = warning.break_price
        stop = warning.stop
        risk = abs(entry - stop)
        direction = 1.0 if warning.side == "BUY" else -1.0
        rr = warning.risk_reward
        first_r = 1.0 if rr >= 2.0 else rr / 3.0
        second_r = 2.0 if rr >= 2.0 else rr * 2.0 / 3.0
        tp1 = entry + direction * risk * first_r
        tp2 = entry + direction * risk * second_r
        tp3 = entry + direction * risk * rr
        confirmed_at = datetime.fromtimestamp(
            (warning.timestamp + 15 * 60 * 1000) / 1000, timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
        rsi_label = {
            "BULLISH": "واگرایی صعودی",
            "BEARISH": "واگرایی نزولی",
            "OPPOSING_BEARISH": "واگرایی نزولی خلاف جهت",
            "OPPOSING_BULLISH": "واگرایی صعودی خلاف جهت",
            "NONE": "واگرایی معتبر مشاهده نشد",
            "DISABLED": "نمایش واگرایی خاموش",
        }.get(warning.rsi_divergence, "نامشخص")
        notify(
            f"🚨 هشدار شکست خط روند + برخورد به اردربلاک | {warning.symbol}\n"
            f"جهت: {warning.side} | تایم‌فریم: ۱۵ دقیقه\n"
            f"وضعیت اردربلاک: برخورد واقعی کندل تأییدشده به محدوده اردربلاک هم‌جهت\n"
            f"واگرایی RSI: {rsi_label}"
            + (f" | RSI: {warning.rsi_value:.1f}" if warning.rsi_value is not None else "")
            + "\n"
            f"ورود پیشنهادی: {entry:.8g}\n"
            f"حد ضرر ساختاری: {stop:.8g}\n"
            f"TP1: {tp1:.8g} | TP2: {tp2:.8g} | TP3: {tp3:.8g}\n"
            f"زمان تأیید: {confirmed_at}\n"
            "وضعیت: هشدار تأیید شد؛ هیچ سفارش لیمیت یا سفارش دیگری از این هشدار ثبت نمی‌شود.\n"
            "این هشدار به‌تنهایی معامله دمو باز نمی‌کند. حساب دمو | معاملات واقعی: خاموش"
        )

    def on_signal(signal) -> None:
        # Pine allows one open position per symbol. Account-wide free-margin
        # limits must not suppress the strategy alert itself.
        existing = runtime.account.positions.get(signal.symbol)
        if existing is not None and not existing.closed:
            return
        position = open_demo_from_signal(signal)
        entry = position.entry if position is not None else signal.entry
        stop = position.stop if position is not None else signal.stop
        direction = 1 if signal.side == "BUY" else -1
        risk_distance = abs(entry - stop)
        first_r = 1.0 if signal.risk_reward >= 2.0 else signal.risk_reward / 3.0
        second_r = 2.0 if signal.risk_reward >= 2.0 else signal.risk_reward * 2.0 / 3.0
        tp1 = position.tp1 if position is not None else entry + direction * risk_distance * first_r
        tp2 = position.tp2 if position is not None else entry + direction * risk_distance * second_r
        tp3 = position.tp3 if position is not None else entry + direction * risk_distance * signal.risk_reward
        method_label = {"ORDER_BLOCK": "OB", "ORDER_BLOCK_SWEEP": "OB + SWEEP", "TRENDLINE": "خط روند مستقل"}.get(signal.method, signal.method)
        notify(
            f"📣 سیگنال استراتژی {signal.side} | {signal.symbol}\n"
            f"روش: {method_label}\n"
            f"ورود: {entry:.8g}\n"
            f"حد ضرر: {stop:.8g}\n"
            f"TP1: {tp1:.8g} | TP2: {tp2:.8g} | TP3: {tp3:.8g}\n"
            + (f"امتیاز اعتبار OB: {signal.ob_score:g}/100\n" if signal.ob_score is not None else "")
            + f"زمان: {datetime.fromtimestamp((signal.timestamp + 15 * 60 * 1000) / 1000, timezone.utc).isoformat()}\n"
            + f"واگرایی RSI: {signal.rsi_divergence} | RSI: {signal.rsi_value}\n"
            + ("معامله در حساب دمو ثبت شد." if position is not None else
               "سیگنال صادر شد؛ ثبت دمو به دلیل محدودیت موجودی/مارجین انجام نشد.")
            + "\nفقط حساب دمو؛ بدون سفارش واقعی."
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

    # Disable any still-unfilled legacy demo limit legs from the previous version.
    # Filled/open demo positions and the trade ledger are deliberately preserved.
    migration_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    cancelled_legacy_legs = []
    for symbol in list(runtime.pending_limits.setups):
        cancelled_legacy_legs.extend(runtime.pending_limits.cancel(
            symbol, migration_time,
            "قابلیت سفارش لیمیت غیرفعال شد؛ سیگنال‌های جدید ورود مستقیم دارند",
        ))
    if cancelled_legacy_legs:
        runtime.persist_pending_limits()
        for item in cancelled_legacy_legs:
            on_pending_event({
                "symbol": item.symbol, "side": item.side,
                "type": "LIMIT_CANCELLED", "leg": item.leg_number,
                "price": item.price,
                "reason": item.reason,
            })

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


async def run_liquidity_scanner(settings: Settings) -> None:
    """Independent 15m liquidity-sweep scanner with its own fixed-margin demo ledger."""
    market_data = WeexMarketData(settings.exchange_id, settings.quote_currency)
    store = LiquidityStore(settings.database_path)
    account, config, enabled, cursors = store.load()
    runtime.liquidity_account = account
    runtime.liquidity_config = config
    runtime.liquidity_enabled = enabled
    runtime.liquidity_store = store
    runtime.liquidity_cursors = cursors
    loop = asyncio.get_running_loop()
    bot = Bot(token=settings.telegram_bot_token) if settings.telegram_bot_token and settings.telegram_chat_id else None
    if bot is not None:
        try:
            await bot.initialize()
        except Exception:
            logger.exception("Liquidity Telegram notifications unavailable; scanning continues.")
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
                logger.exception("Liquidity Telegram notification failed.")
        future.add_done_callback(report_error)

    def on_signal(signal, position) -> None:
        notify(
            f"💧 سیگنال جمع‌آوری نقدینگی | {signal.symbol}\\n"
            f"جهت: {signal.side} | تایم‌فریم: ۱۵ دقیقه\\n"
            f"ورود: {signal.entry:.8g}\\n"
            f"حد ضرر: {signal.stop:.8g}\\n"
            f"حد سود: {signal.target:.8g} ({signal.risk_reward:g}R)\\n"
            f"EMA: {signal.ema:.8g} | ATR: {signal.atr:.8g}\\n"
            f"امتیاز کیفیت: {signal.score:.1f}/100\\n"
            + ("ثبت در حساب دمو مستقل انجام شد.\\n" if position is not None else
               "سیگنال صادر شد؛ ثبت دمو به دلیل محدودیت مارجین انجام نشد.\\n")
            + "مارجین ثابت: ۵ USDT | اهرم: ۵x | ارزش اسمی: ۲۵ USDT\\n"
            + "فقط دمو؛ معامله واقعی خاموش."
        )

    def on_trade_event(event: dict) -> None:
        label = "حد سود فعال شد" if event.get("type") == "TP" else "حد ضرر فعال شد"
        notify(
            f"💧 نتیجه معامله جمع‌آوری نقدینگی | {event.get('symbol', '')}\\n"
            f"{label} | قیمت خروج: {event.get('price', 0):.8g}\\n"
            f"سود/زیان خالص: {event.get('realized_pnl', 0):+.4f} USDT\\n"
            "حساب دمو مستقل؛ معاملات واقعی خاموش."
        )

    scanner = LiquidityScanner(
        market_data=market_data, account=account, config=config, store=store,
        enabled=enabled, cursors=cursors, on_signal=on_signal,
        on_trade_event=on_trade_event,
    )
    runtime.liquidity_scanner = scanner
    logger.info("Liquidity sweep scanner started; 15m only; manual WEEX symbols; separate demo account.")
    try:
        while True:
            scanner.config = runtime.liquidity_config or scanner.config
            scanner.enabled = runtime.liquidity_enabled
            runtime.liquidity_scanner_state = "RUNNING" if scanner.enabled else "PAUSED"
            summary = await asyncio.to_thread(scanner.scan_once, list(runtime.manual_symbols))
            runtime.liquidity_cursors = scanner.cursors
            logger.info("Liquidity scan completed: %s", summary)
            await asyncio.sleep(settings.scan_interval_seconds)
    finally:
        runtime.liquidity_scanner_state = "STOPPED"
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
        run_liquidity_scanner(settings),
    )


if __name__ == "__main__":
    asyncio.run(main())
