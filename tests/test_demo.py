import pytest

from app.demo import DemoAccount, Side
from app.storage import SQLiteStore


def test_demo_position_uses_fixed_margin_and_five_x_leverage():
    account = DemoAccount()
    position = account.open_position("BTC/USDT", Side.LONG, 100.0, 98.0)
    assert account.notional == 25.0
    assert position.quantity_initial == pytest.approx(0.25)
    assert position.tp1 == pytest.approx(102.0)
    assert position.tp2 == pytest.approx(104.0)
    assert position.tp3 == pytest.approx(106.0)


def test_tp1_and_tp2_move_stop_as_agreed():
    account = DemoAccount(fee_rate=0)
    position = account.open_position("BTC/USDT", Side.LONG, 100.0, 98.0)
    events = account.process_candle(high=102.0, low=100.5)
    assert [event["type"] for event in events] == ["TP1"]
    assert position.stop == pytest.approx(100.0)
    events = account.process_candle(high=104.0, low=102.5)
    assert [event["type"] for event in events] == ["TP2"]
    assert position.stop == pytest.approx(102.0)


def test_stop_has_priority_when_same_candle_touches_stop_and_target():
    account = DemoAccount(fee_rate=0)
    position = account.open_position("BTC/USDT", Side.LONG, 100.0, 98.0)
    events = account.process_candle(high=103.0, low=97.0)
    assert [event["type"] for event in events] == ["STOP"]
    assert position.closed


def test_short_targets_are_below_entry():
    account = DemoAccount(fee_rate=0)
    position = account.open_position("ETH/USDT", Side.SHORT, 100.0, 102.0)
    assert position.tp1 == pytest.approx(98.0)
    assert position.tp2 == pytest.approx(96.0)
    assert position.tp3 == pytest.approx(94.0)


def test_multiple_demo_positions_reserve_fixed_margin_each():
    account = DemoAccount(fee_rate=0)
    account.open_position("BTC/USDT", Side.LONG, 100.0, 98.0)
    account.open_position("ETH/USDT", Side.SHORT, 50.0, 51.0)
    assert len(account.open_positions) == 2
    assert account.margin_used == pytest.approx(10.0)
    assert account.free_margin == pytest.approx(90.0)


def test_multiple_positions_require_symbol_when_processing_candle():
    account = DemoAccount(fee_rate=0)
    account.open_position("BTC/USDT", Side.LONG, 100.0, 98.0)
    account.open_position("ETH/USDT", Side.SHORT, 50.0, 51.0)
    with pytest.raises(ValueError):
        account.process_candle(high=101.0, low=99.0)
    events = account.process_candle(high=102.0, low=101.0, symbol="BTC/USDT")
    assert [event["type"] for event in events] == ["TP1"]


def test_sqlite_restores_balance_positions_partial_tp_and_events(tmp_path):
    path = str(tmp_path / "demo.sqlite3")
    store = SQLiteStore(path)
    account = DemoAccount(fee_rate=0)
    position = account.open_position("BTC/USDT", Side.LONG, 100.0, 98.0)
    account.process_candle(high=102.0, low=100.5, symbol="BTC/USDT")
    events = [{"symbol": "BTC/USDT", "type": "TP1", "price": 102.0,
               "realized_pnl": position.realized_pnl}]
    store.save_state(account, events)

    restored, restored_events = SQLiteStore(path).load_state(DemoAccount())
    restored_position = restored.positions["BTC/USDT"]
    assert restored.balance == pytest.approx(account.balance)
    assert restored_position.tp1_done is True
    assert restored_position.stop == pytest.approx(100.0)
    assert restored_position.quantity_remaining == pytest.approx(position.quantity_remaining)
    assert restored_position.realized_pnl == pytest.approx(position.realized_pnl)
    assert restored_events == events


def test_sqlite_restores_closed_position_and_account_configuration(tmp_path):
    path = str(tmp_path / "closed.sqlite3")
    store = SQLiteStore(path)
    account = DemoAccount(initial_balance=250, margin_per_trade=10, leverage=3, fee_rate=0)
    position = account.open_position("ETH/USDT", Side.SHORT, 100, 102)
    account.process_candle(high=99.5, low=98.5, symbol="ETH/USDT")
    account.process_candle(high=97.5, low=96.5, symbol="ETH/USDT")
    account.process_candle(high=95, low=94, symbol="ETH/USDT")
    store.save_state(account, [{"symbol": "ETH/USDT", "type": "TP3", "price": 94}])

    restored, events = store.load_state(DemoAccount())
    assert restored.initial_balance == pytest.approx(250)
    assert restored.margin_per_trade == pytest.approx(10)
    assert restored.leverage == pytest.approx(3)
    assert restored.positions["ETH/USDT"].closed is True
    assert restored.positions["ETH/USDT"].tp3_done is True
    assert len(events) == 1


def test_sqlite_empty_database_keeps_configured_defaults(tmp_path):
    store = SQLiteStore(str(tmp_path / "empty.sqlite3"))
    defaults = DemoAccount(initial_balance=150, margin_per_trade=7, leverage=4)
    restored, events = store.load_state(defaults)
    assert restored is defaults
    assert events == []
