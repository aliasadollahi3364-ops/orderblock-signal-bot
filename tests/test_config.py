import pytest

from app.config import Settings


def test_demo_only_is_the_default_and_weex_public_market_data_is_allowed():
    settings = Settings(_env_file=None)

    assert settings.exchange_id == "weex"
    assert settings.live_trading_enabled is False
    settings.assert_demo_only()


def test_demo_only_guard_rejects_live_trading_even_when_explicitly_enabled():
    settings = Settings(_env_file=None, live_trading_enabled=True)

    with pytest.raises(RuntimeError, match="demo-only"):
        settings.assert_demo_only()


def test_demo_only_guard_rejects_non_weex_exchange_adapter():
    settings = Settings(_env_file=None, exchange_id="binance")

    with pytest.raises(RuntimeError, match="Only the WEEX public market-data adapter"):
        settings.assert_demo_only()
