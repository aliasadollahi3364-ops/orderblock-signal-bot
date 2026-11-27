import pytest

from app.liquidity import (
    BAR_MS, LiquidityConfig, LiquidityDemoAccount, LiquidityScanner,
    LiquiditySignal, LiquidityStore, LiquiditySweepStrategy,
)
from app.strategy import Candle


def make_signal(symbol="BTC/USDT", side="BUY", ts=0, entry=100.0, stop=98.0, target=106.0):
    return LiquiditySignal(symbol, side, ts, entry, stop, target, 3.0, 80.0, 99.0, 1.0)


def test_liquidity_demo_uses_fixed_five_margin_and_five_x_leverage():
    account = LiquidityDemoAccount(fee_rate=0)
    position = account.open_position(make_signal())
    assert account.initial_balance == 100
    assert account.margin_per_trade == 5
    assert account.leverage == 5
    assert account.notional == 25
    assert position.quantity == pytest.approx(0.25)
    assert account.margin_used == 5
    assert account.free_margin == 95


def test_liquidity_demo_single_target_and_stop_priority():
    account = LiquidityDemoAccount(fee_rate=0)
    position = account.open_position(make_signal())
    event = account.process_candle("BTC/USDT", high=107, low=97, timestamp=BAR_MS * 2)
    assert event["type"] == "STOP"
    assert position.closed
    assert position.realized_pnl == pytest.approx(-0.5)
    assert account.margin_used == 0


def test_liquidity_demo_take_profit_closes_entire_position():
    account = LiquidityDemoAccount(fee_rate=0)
    position = account.open_position(make_signal())
    event = account.process_candle("BTC/USDT", high=106, low=100, timestamp=BAR_MS * 2)
    assert event["type"] == "TP"
    assert position.closed
    assert position.realized_pnl == pytest.approx(1.5)


def test_liquidity_account_and_config_restore(tmp_path):
    store = LiquidityStore(str(tmp_path / "bot.sqlite3"))
    account = LiquidityDemoAccount(fee_rate=0)
    account.open_position(make_signal())
    config = LiquidityConfig(use_session_filter=True, use_ema_filter=False)
    cursors = {"BTC/USDT": BAR_MS * 10}
    store.save(account, config, False, cursors)
    restored, restored_config, enabled, restored_cursors = store.load()
    assert restored.balance == pytest.approx(account.balance)
    assert restored.open_positions[0].quantity == pytest.approx(0.25)
    assert restored.margin_used == pytest.approx(5)
    assert restored_config.use_session_filter is True
    assert restored_config.use_ema_filter is False
    assert enabled is False
    assert restored_cursors == cursors


def test_liquidity_scanner_requests_only_15m_and_only_manual_symbols(tmp_path):
    class Market:
        symbol = "BTC/USDT"

    class FakeMarketData:
        def __init__(self):
            self.calls = []
        def resolve_manual_symbol(self, requested):
            assert requested == "BTC"
            return Market()
        def fetch_ohlcv(self, symbol, timeframe, limit):
            self.calls.append((symbol, timeframe, limit))
            return [[i * BAR_MS, 100, 101, 99, 100, 10] for i in range(1, 100)]

    market_data = FakeMarketData()
    store = LiquidityStore(str(tmp_path / "scan.sqlite3"))
    scanner = LiquidityScanner(market_data, LiquidityDemoAccount(), LiquidityConfig(), store)
    scanner.scan_once(["BTC"])
    assert market_data.calls
    assert all(call[1] == "15m" for call in market_data.calls)
    assert all(call[0] == "BTC/USDT" for call in market_data.calls)


def test_disabled_liquidity_scanner_does_not_fetch(tmp_path):
    class NeverFetch:
        def resolve_manual_symbol(self, requested):
            raise AssertionError("must not resolve while disabled")
    store = LiquidityStore(str(tmp_path / "disabled.sqlite3"))
    scanner = LiquidityScanner(NeverFetch(), LiquidityDemoAccount(), LiquidityConfig(), store, enabled=False)
    assert scanner.scan_once(["BTC"]) == {"symbols": 0, "signals": 0, "events": 0, "errors": 0}


def test_strategy_rejects_sweep_below_minimum_quality_score():
    cfg = LiquidityConfig(
        ema_length=3, use_ema_filter=False, pivot_length=2, atr_length=2,
        min_quality_score=100,
    )
    engine = LiquiditySweepStrategy("BTC/USDT", cfg)
    bars = [
        Candle(0 * BAR_MS, 100, 102, 99, 101, 10),
        Candle(1 * BAR_MS, 101, 102, 98, 100, 10),
        Candle(2 * BAR_MS, 100, 101, 95, 99, 10),
        Candle(3 * BAR_MS, 99, 102, 98, 101, 10),
        Candle(4 * BAR_MS, 101, 103, 99, 102, 10),
        Candle(5 * BAR_MS, 97, 100, 94, 99, 10),
    ]
    signals = [engine.on_bar(bar) for bar in bars]
    assert signals[-1] is None
    assert engine.pending_bull is None


def test_liquidity_config_rejects_quality_score_out_of_range():
    with pytest.raises(ValueError, match="quality score"):
        LiquidityConfig(min_quality_score=100.1)


def test_strategy_detects_confirmed_pivot_low_sweep():
    cfg = LiquidityConfig(ema_length=3, use_ema_filter=False, pivot_length=2, atr_length=2)
    engine = LiquiditySweepStrategy("BTC/USDT", cfg)
    bars = [
        Candle(0 * BAR_MS, 100, 102, 99, 101, 10),
        Candle(1 * BAR_MS, 101, 102, 98, 100, 10),
        Candle(2 * BAR_MS, 100, 101, 95, 99, 10),
        Candle(3 * BAR_MS, 99, 102, 98, 101, 10),
        Candle(4 * BAR_MS, 101, 103, 99, 102, 10),
        Candle(5 * BAR_MS, 97, 100, 94, 99, 10),
    ]
    signals = [engine.on_bar(bar) for bar in bars]
    found = signals[-1]
    assert found is not None
    assert found.side == "BUY"
    assert found.entry == 99
    assert found.stop < found.entry < found.target
    assert found.risk_reward == 3.0
