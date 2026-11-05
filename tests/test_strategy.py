from app.strategy import Candle, OrderBlockStrategy, StrategyConfig


def bar(ts, o, h, l, c):
    return Candle(ts, o, h, l, c)


def test_confirmed_bullish_bos_creates_nearest_prior_bearish_order_block():
    cfg = StrategyConfig(pivot4=1, body_len=2, ob_search=3)
    engine = OrderBlockStrategy("BTC/USDT", cfg)

    engine.on_4h_close(bar(1, 10, 11, 8, 9))       # bearish OB candidate
    engine.on_4h_close(bar(2, 9, 12, 8.5, 10))     # pivot-high candidate
    engine.on_4h_close(bar(3, 10, 11, 9, 10.5))    # confirms pivot high
    created = engine.on_4h_close(bar(4, 10.5, 13.5, 10, 13.0))

    assert engine.trend4h == 1
    assert len(created) == 1
    assert created[0].direction == 1
    assert created[0].origin_time == 1
    assert created[0].top == 11
    assert created[0].bottom == 8


def test_bullish_ob_invalidates_only_on_confirmed_4h_close_below_bottom():
    cfg = StrategyConfig(pivot4=1, body_len=2, ob_search=3)
    engine = OrderBlockStrategy("BTC/USDT", cfg)
    engine.on_4h_close(bar(1, 10, 11, 8, 9))
    engine.on_4h_close(bar(2, 9, 12, 8.5, 10))
    engine.on_4h_close(bar(3, 10, 11, 9, 10.5))
    created = engine.on_4h_close(bar(4, 10.5, 13.5, 10, 13))
    assert created[0].active

    engine.on_4h_close(bar(5, 9, 10, 7, 7.5))
    assert not created[0].active


def test_rejects_duplicate_or_out_of_order_candles():
    engine = OrderBlockStrategy("BTC/USDT")
    engine.on_4h_close(bar(10, 10, 11, 9, 10.5))
    try:
        engine.on_4h_close(bar(10, 10, 11, 9, 10.5))
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate timestamp should be rejected")


def test_15m_touch_and_confirmation_emit_buy_with_ob_boundary_stop():
    from app.strategy import OrderBlock

    cfg = StrategyConfig(
        pivot15=1,
        atr_len=1,
        require_ob_touch=True,
        use_touch_freshness=True,
        use_choch_filter=False,
        use_retest_filter=False,
        use_confirm_candle=True,
        use_confirm_strength=False,
        use_ob_boundary_sl=True,
        min_tick=0.001,
    )
    engine = OrderBlockStrategy("BTC/USDT", cfg)
    engine.last_pl15 = 95.0
    ob = OrderBlock(direction=1, top=100.0, bottom=98.0, origin_time=123)
    engine.order_blocks.append(ob)

    signal = engine.on_15m_close(bar(1000, 99.5, 101.5, 99.0, 101.0))

    assert signal is not None
    assert signal.side == "BUY"
    assert signal.entry == 101.0
    assert signal.stop < 98.0
    assert ob.used
