import pytest

from app.demo import DemoAccount, Side
from app.storage import SQLiteStore
from app.strategy import StrategyConfig


def test_demo_position_uses_fixed_margin_and_five_x_leverage():
    account = DemoAccount()
    position = account.open_position("BTC/USDT", Side.LONG, 100.0, 98.0)
    assert account.notional == 25.0
    assert position.quantity_initial == pytest.approx(0.25)
    assert position.tp1 == pytest.approx(102.0)
    assert position.tp2 == pytest.approx(104.0)
    assert position.tp3 == pytest.approx(106.0)



def test_two_limit_fills_aggregate_with_weighted_average_and_five_dollars_each():
    account = DemoAccount(fee_rate=0)
    first = account.add_limit_fill("BTC/USDT", Side.LONG, 100.0, 95.0)
    assert first.entry == pytest.approx(100.0)
    assert first.quantity_initial == pytest.approx(25.0 / 100.0)
    assert account.margin_used == pytest.approx(5.0)

    second = account.add_limit_fill("BTC/USDT", Side.LONG, 98.0, 95.0)
    assert second is first
    assert second.entry == pytest.approx((100.0 * 0.25 + 98.0 * (25.0 / 98.0)) / (0.25 + 25.0 / 98.0))
    assert second.quantity_initial == pytest.approx(25.0 / 100.0 + 25.0 / 98.0)
    assert second.quantity_remaining == pytest.approx(second.quantity_initial)
    assert account.margin_used == pytest.approx(10.0)
    assert second.tp1 == pytest.approx(second.entry + (second.entry - 95.0))
    assert second.margin_allocated == pytest.approx(10.0)


def test_two_limit_fills_reject_third_leg_or_conflicting_direction():
    account = DemoAccount(fee_rate=0)
    account.add_limit_fill("BTC/USDT", Side.LONG, 100.0, 95.0)
    account.add_limit_fill("BTC/USDT", Side.LONG, 98.0, 95.0)
    with pytest.raises(ValueError):
        account.add_limit_fill("BTC/USDT", Side.LONG, 97.0, 95.0)
    with pytest.raises(ValueError):
        account.add_limit_fill("BTC/USDT", Side.SHORT, 100.0, 105.0)



def test_partial_limit_position_restores_allocated_margin_and_accepts_second_leg(tmp_path):
    store = SQLiteStore(str(tmp_path / "two-legs.sqlite3"))
    account = DemoAccount(fee_rate=0)
    account.add_limit_fill("BTC/USDT", Side.LONG, 100.0, 95.0)
    store.save_state(account, [])
    restored, _ = store.load_state(DemoAccount())
    assert restored.margin_used == pytest.approx(5.0)
    position = restored.add_limit_fill("BTC/USDT", Side.LONG, 98.0, 95.0)
    assert position.margin_allocated == pytest.approx(10.0)
    assert restored.margin_used == pytest.approx(10.0)
    assert position.quantity_initial == pytest.approx(25.0 / 100.0 + 25.0 / 98.0)


def test_two_limit_fills_use_one_average_entry_and_shared_targets():
    account = DemoAccount(fee_rate=0)
    account.add_limit_fill("BTC/USDT", Side.LONG, 100.0, 95.0, leg_number=1)
    account.add_limit_fill("BTC/USDT", Side.LONG, 98.0, 95.0, leg_number=2)
    position = account.positions["BTC/USDT"]
    expected_entry = (100.0 * 0.25 + 98.0 * (25.0 / 98.0)) / (0.25 + 25.0 / 98.0)
    risk = expected_entry - 95.0
    assert position.entry == pytest.approx(expected_entry)
    assert position.tp1 == pytest.approx(expected_entry + risk)
    assert position.tp2 == pytest.approx(expected_entry + 2 * risk)
    assert position.tp3 == pytest.approx(expected_entry + 3 * risk)
    events = account.process_candle(high=103.5, low=99.5, symbol='BTC/USDT')
    assert [event['type'] for event in events] == ['TP1']
    assert position.tp1_done is True
    assert position.stop == pytest.approx(expected_entry)
    assert position.quantity_remaining == pytest.approx(position.quantity_initial * 0.67)

def test_pine_risk_sized_entry_uses_three_targets_and_partial_exits():
    account = DemoAccount(fee_rate=0)
    position = account.open_position(
        "BTC/USDT", Side.LONG, 100.0, 98.0,
        risk_percent=1.0, risk_reward=2.5,
    )

    # 1% of 100 USDT is 1 USDT risk; 2 USDT price risk gives 0.5 BTC.
    assert position.quantity_initial == pytest.approx(0.5)
    assert account.margin_used == pytest.approx(10.0)
    assert position.tp1 == pytest.approx(102.0)
    assert position.tp2 == pytest.approx(104.0)
    assert position.tp3 == pytest.approx(105.0)
    assert position.single_target is False

    events = account.process_candle(high=102.1, low=101.0, symbol="BTC/USDT")
    assert [event["type"] for event in events] == ["TP1"]
    assert position.quantity_remaining == pytest.approx(position.quantity_initial * 0.67)
    assert position.stop == pytest.approx(100.0)

    events = account.process_candle(high=104.1, low=102.1, symbol="BTC/USDT")
    assert [event["type"] for event in events] == ["TP2"]
    assert position.quantity_remaining == pytest.approx(position.quantity_initial * 0.34)
    assert position.stop == pytest.approx(102.0)

    events = account.process_candle(high=105.1, low=104.1, symbol="BTC/USDT")
    assert [event["type"] for event in events] == ["TP3"]
    assert position.closed is True
    assert position.quantity_remaining == pytest.approx(0.0)


def test_pine_risk_sized_short_targets_are_below_entry():
    account = DemoAccount(fee_rate=0)
    position = account.open_position(
        "ETH/USDT", Side.SHORT, 100.0, 102.0,
        risk_percent=1.0, risk_reward=2.5,
    )
    assert position.tp1 == pytest.approx(98.0)
    assert position.tp2 == pytest.approx(96.0)
    assert position.tp3 == pytest.approx(95.0)


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


def test_sqlite_does_not_enable_unimplemented_demo_trailing_stop(tmp_path):
    store = SQLiteStore(str(tmp_path / "strategy.sqlite3"))
    requested = StrategyConfig(use_trailing_stop=True)
    store.save_strategy_config(requested)
    restored = store.load_strategy_config()
    assert restored.use_trailing_stop is False



def test_unrealized_pnl_uses_single_weighted_average_after_partial_target():
    account = DemoAccount(fee_rate=0)
    account.add_limit_fill("BTC/USDT", Side.LONG, 100.0, 95.0, leg_number=1)
    account.add_limit_fill("BTC/USDT", Side.LONG, 98.0, 95.0, leg_number=2)
    account.process_candle(high=103.5, low=98.5, symbol="BTC/USDT")
    position = account.positions["BTC/USDT"]
    expected = (101.0 - position.entry) * position.quantity_remaining
    assert account.unrealized_pnl(101.0, "BTC/USDT") == pytest.approx(expected)



def test_two_short_limit_fills_share_weighted_entry_targets_and_break_even_stop():
    account = DemoAccount(fee_rate=0)
    first = account.add_limit_fill("ETH/USDT", Side.SHORT, 100.0, 105.0, leg_number=1)
    second = account.add_limit_fill("ETH/USDT", Side.SHORT, 102.0, 105.0, leg_number=2)

    expected_entry = (100.0 * (25.0 / 100.0) + 102.0 * (25.0 / 102.0)) / (
        25.0 / 100.0 + 25.0 / 102.0
    )
    risk = 105.0 - expected_entry
    assert second is first
    assert second.entry == pytest.approx(expected_entry)
    assert second.tp1 == pytest.approx(expected_entry - risk)
    assert second.tp2 == pytest.approx(expected_entry - 2 * risk)
    assert second.tp3 == pytest.approx(expected_entry - 3 * risk)
    assert account.margin_used == pytest.approx(10.0)

    events = account.process_candle(high=100.0, low=second.tp1, symbol="ETH/USDT")
    assert [event["type"] for event in events] == ["TP1"]
    assert second.stop == pytest.approx(expected_entry)
    assert second.closed is False

    events = account.process_candle(high=expected_entry, low=99.0, symbol="ETH/USDT")
    assert [event["type"] for event in events] == ["BREAK_EVEN_STOP"]
    assert second.closed is True
    assert second.quantity_remaining == pytest.approx(0.0)


def test_two_limit_short_position_stop_wins_if_same_candle_touches_stop_and_target():
    account = DemoAccount(fee_rate=0)
    account.add_limit_fill("ETH/USDT", Side.SHORT, 100.0, 105.0, leg_number=1)
    position = account.add_limit_fill("ETH/USDT", Side.SHORT, 102.0, 105.0, leg_number=2)

    events = account.process_candle(high=106.0, low=position.tp1 - 1.0, symbol="ETH/USDT")
    assert [event["type"] for event in events] == ["STOP"]
    assert position.closed is True
    assert position.quantity_remaining == pytest.approx(0.0)
    assert account.margin_used == pytest.approx(0.0)
