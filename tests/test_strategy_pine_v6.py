from app.strategy_pine_v6 import Candle, OrderBlock, OrderBlockStrategy, StrategyConfig


def bar(ts, o, h, l, c, v=100.0):
    return Candle(ts, o, h, l, c, v)


def test_pine_v6_defaults_and_minimum_ob_score():
    cfg = StrategyConfig()
    assert cfg.min_ob_score == 70
    assert cfg.enable_trendline_signals is True
    assert cfg.use_trend_filter is True
    assert cfg.require_trendline_pullback is True
    assert cfg.use_sma200_filter is True
    assert cfg.use_sma200_trendline_filter is True


def test_independent_trendline_entry_does_not_require_an_order_block():
    cfg = StrategyConfig(
        pivot15=3, atr_len=1, use_sma200_trendline_filter=False,
        use_sma200_filter=False, sl_buffer_atr=0.1,
        trendline_retest_atr=0.2, enable_trendline_signals=True,
    )
    engine = OrderBlockStrategy("BTC/USDT", cfg)
    engine.candles15.append(bar(1, 104.0, 105.0, 103.0, 105.0))
    engine._last_15m_timestamp = 1
    engine._tr15.append(2.0)
    engine._atr15_value = 2.0
    engine.third_ph15, engine.third_ph15_bar = 110.0, -2
    engine.prev_ph15, engine.prev_ph15_bar = 108.0, -1
    engine.last_ph15, engine.last_ph15_bar = 106.0, 0
    engine.last_pl15 = 90.0

    breakout = engine.on_15m_close(bar(2, 105.0, 107.5, 104.5, 107.0))
    assert breakout is None
    assert engine._long_pending is True
    assert engine.order_blocks == []

    signal = engine.on_15m_close(bar(3, 104.0, 104.8, 103.9, 104.5))
    assert signal is not None
    assert signal.method == "TRENDLINE"
    assert signal.side == "BUY"
    assert signal.ob_top is None
    assert signal.ob_bottom is None


def test_order_block_sweep_waits_for_a_later_confirmation_candle():
    cfg = StrategyConfig(
        pivot15=1, atr_len=1, use_trend_filter=False,
        require_ob_touch=False, use_touch_freshness=False,
        use_choch_strength=False, use_confirm_strength=False,
        use_sma200_filter=False, use_sma200_trendline_filter=False,
        enable_trendline_signals=False, sl_buffer_atr=0.1,
        min_tick=0.001,
    )
    engine = OrderBlockStrategy("BTC/USDT", cfg)
    engine.candles15.append(bar(1, 98.0, 99.0, 97.5, 98.5))
    engine._last_15m_timestamp = 1
    engine._tr15.append(1.5)
    engine._atr15_value = 1.5
    engine.last_ph15 = 99.0
    engine.last_pl15 = 95.0
    ob = OrderBlock(1, 100.0, 98.0, 100, 100.0)
    engine.order_blocks.append(ob)

    # CHoCH: close breaks the last confirmed 15m high after initial OB touch.
    assert engine.on_15m_close(bar(2, 98.8, 100.0, 98.2, 99.5)) is None
    assert ob.has_choch is True

    # Sweep: wick runs below the bullish OB and candle closes back inside.
    assert engine.on_15m_close(bar(3, 99.2, 99.6, 97.5, 98.8)) is None
    assert ob.sweep_bar == 2
    assert ob.sweep_extreme == 97.5

    # Entry only after a later bullish candle closes above the sweep candle high.
    signal = engine.on_15m_close(bar(4, 99.0, 100.8, 98.7, 100.5))
    assert signal is not None
    assert signal.method == "ORDER_BLOCK_SWEEP"
    assert signal.side == "BUY"
    assert signal.stop < ob.sweep_extreme
    assert ob.used is True
