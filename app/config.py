"""Environment-backed application settings."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    exchange_id: str = "weex"
    quote_currency: str = "USDT"
    timeframe_entry: str = "15m"
    timeframe_order_block: str = "4h"
    scan_interval_seconds: int = Field(default=60, ge=10)
    scanner_universe_limit: int = Field(default=500, ge=1, le=2000)
    scanner_candidate_limit: int = Field(default=60, ge=1, le=500)
    demo_initial_balance: float = Field(default=100.0, gt=0)
    demo_margin_per_trade: float = Field(default=5.0, gt=0)
    demo_leverage: float = Field(default=5.0, gt=0)
    database_path: str = "./data/demo.sqlite3"
    live_trading_enabled: bool = False
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)

    def assert_demo_only(self) -> None:
        if self.live_trading_enabled:
            raise RuntimeError(
                "LIVE_TRADING_ENABLED=true is prohibited: this application is demo-only."
            )
