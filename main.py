"""Demo-only application entrypoint."""
from __future__ import annotations

import asyncio
import logging

import uvicorn

from app.api import app
from app.config import Settings
from app.market_data import WeexMarketData
from app.scanner import AutomaticScanner, open_demo_from_signal
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


async def run_scanner(settings: Settings) -> None:
    market_data = WeexMarketData(settings.exchange_id, settings.quote_currency)
    scanner = AutomaticScanner(
        market_data=market_data,
        universe_limit=settings.scanner_universe_limit,
        candidate_limit=settings.scanner_candidate_limit,
        on_signal=open_demo_from_signal,
    )
    try:
        await asyncio.to_thread(scanner.run_forever, settings.scan_interval_seconds)
    finally:
        market_data.close()


async def main() -> None:
    settings = Settings()
    settings.assert_demo_only()
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info"))
    await asyncio.gather(
        server.serve(),
        run_telegram(settings.telegram_bot_token, settings.telegram_chat_id),
        run_scanner(settings),
    )


if __name__ == "__main__":
    asyncio.run(main())
