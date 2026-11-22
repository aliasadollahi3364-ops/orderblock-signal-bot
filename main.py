"""Minimal demo + Iranian 18k gold application entrypoint."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import uvicorn
from telegram import Bot

from app.api import app
from app.config import Settings
from app.demo import DemoAccount
from app.gold_market import GoldMarketService, format_gold_signal
from app.market_data import WeexMarketData
from app.scanner import AutomaticScanner, open_demo_from_signal
from app.runtime import runtime
from app.storage import SQLiteStore
from app.strategy import StrategyConfig
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

    def on_signal(signal) -> None:
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
        on_trade_event=on_trade_event,
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
    # Keep the user's manually selected symbols and the existing demo ledger.
    runtime.manual_symbols = store.load_manual_symbols()
    runtime.selected_symbols = []
    runtime.signal_count = 0
    runtime.scanner_state = "WAITING_FOR_MANUAL_SYMBOLS"
    # Start from the exact defaults in the supplied Pine v6 strategy.
    runtime.strategy_config = StrategyConfig(rsi_divergence_pivot_length=5)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info"))
    await asyncio.gather(
        server.serve(),
        run_telegram(settings.telegram_bot_token, settings.telegram_chat_id),
        run_gold_scanner(settings),
        run_crypto_scanner(settings),
    )


if __name__ == "__main__":
    asyncio.run(main())
