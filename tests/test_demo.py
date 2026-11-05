import pytest

from app.demo import DemoAccount, Side


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
