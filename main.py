"""Demo-only application entrypoint."""
from __future__ import annotations

import asyncio
import logging

import uvicorn
from telegram import Bot

from app.api import app
from app.config import Settings
from app.market_data import WeexMarketData
from app.scanner import AutomaticScanner, open_demo_from_signal
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
    bot = Bot(token=settings.telegram_bot_token) if settings.telegram_bot_token and settings.telegram_chat_id else None

    def send(text: str) -> None:
        if bot is not None:
            future = asyncio.run_coroutine_threadsafe(
                bot.send_message(chat_id=settings.telegram_chat_id, text=text),
                loop,
            )
            future.add_done_callback(
                lambda result: logger.error("Telegram notification failed: %s", result.exception())
                if result.exception() else None
            )

    def handle_signal(signal: Signal) -> None:
        position = open_demo_from_signal(signal)
        if position is None:
            return
        send(
            f"📣 سیگنال جدید دمو | {signal.symbol}\n"
            f"جهت: {signal.side}\nورود: {signal.entry:g}\n"
            f"حد ضرر: {signal.stop:g}\n"
            f"TP1: {position.tp1:g}\nTP2: {position.tp2:g}\nTP3: {position.tp3:g}\n"
            f"زمان کندل: {signal.timestamp}\nمعاملات واقعی: خاموش"
        )

    def handle_trade_event(event: dict) -> None:
        send(
            f"🔔 رویداد معامله دمو | {event['symbol']}\n"
            f"وضعیت: {event['type']}\nقیمت: {event['price']:g}\n"
            f"سود/زیان تحقق‌یافته: {event['realized_pnl']:+.4f} USDT\n"
            "معاملات واقعی: خاموش"
        )

    scanner = AutomaticScanner(
        market_data=market_data,
        universe_limit=settings.scanner_universe_limit,
        candidate_limit=settings.scanner_candidate_limit,
        on_signal=handle_signal,
        on_trade_event=handle_trade_event,
    )
    try:
        await asyncio.to_thread(scanner.run_forever, settings.scan_interval_seconds)
    finally:
        market_data.close()


async def main() -> None:
    settings = Settings()
    settings.assert_demo_only()
    loop = asyncio.get_running_loop()
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info"))
    await asyncio.gather(
        server.serve(),
        run_telegram(settings.telegram_bot_token, settings.telegram_chat_id),
        run_scanner(settings, loop),
    )


if __name__ == "__main__":
    asyncio.run(main())
