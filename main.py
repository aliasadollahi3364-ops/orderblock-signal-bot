"""Demo-only application entrypoint: FastAPI plus optional Telegram polling."""
from __future__ import annotations

import asyncio
import logging

import uvicorn

from app.api import app
from app.config import Settings
from app.telegram_bot import build_telegram_application

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orderblock-signal-bot")


async def run_telegram(token: str, chat_id: str) -> None:
    if not token or not chat_id:
        logger.warning("Telegram is disabled until both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are configured.")
        return
    application = build_telegram_application(token, chat_id)
    await application.initialize()
    await application.start()
    if application.updater is None:
        raise RuntimeError("Telegram updater is unavailable.")
    await application.updater.start_polling()
    logger.info("Telegram demo status bot started.")
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


async def main() -> None:
    settings = Settings()
    settings.assert_demo_only()
    server = uvicorn.Server(
        uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info")
    )
    await asyncio.gather(server.serve(), run_telegram(settings.telegram_bot_token, settings.telegram_chat_id))


if __name__ == "__main__":
    asyncio.run(main())
