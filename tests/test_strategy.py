from app.strategy import Candle, OrderBlockStrategy, StrategyConfig


def bar(ts, o, h, l, c):
    return Candle(ts, o, h, l, c)


def test_confirmed_bullish_bos_creates_nearest_prior_bearish_order_block():
    cfg = StrategyConfig(pivot4=1, body_len=2, ob_search=3, ob_atr_len=1)
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
    cfg = StrategyConfig(pivot4=1, body_len=2, ob_search=3, ob_atr_len=1)
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
        use_trend_filter=False,
        use_sma200_filter=False,
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



def test_new_pine_defaults_match_supplied_source():
    cfg = StrategyConfig()
    assert cfg.use_trend_filter is True
    assert cfg.ob_atr_len == 14
    assert cfg.min_bos_body_atr == 0.65
    assert cfg.min_bos_body_avg == 1.20
    assert cfg.min_break_atr == 0.10
    assert cfg.max_ob_candle_range_atr == 2.50
    assert cfg.risk_reward == 2.5
    assert cfg.sl_buffer_atr == 2.0
    assert cfg.enable_trendline_signals is True
    assert cfg.require_trendline_pullback is True
    assert cfg.use_sma200_filter is True
    assert cfg.use_sma200_trendline_filter is True
    assert cfg.use_trailing_stop is False


def test_nearest_touched_ob_direction_includes_used_but_not_inactive_zones():
    from app.strategy import OrderBlock

    engine = OrderBlockStrategy("BTC/USDT")
    engine.order_blocks.extend([
        OrderBlock(1, 105.0, 100.0, 1, used=True),
        OrderBlock(-1, 112.0, 109.0, 2),
        OrderBlock(1, 104.0, 101.0, 3, active=False),
    ])
    candle = bar(100, 108.0, 110.0, 103.0, 108.5)
    assert engine._nearest_touched_ob_direction(candle) == -1


def test_trendline_signal_configuration_is_independent_from_ob_trend_filter():
    cfg = StrategyConfig(use_trend_filter=False, enable_trendline_signals=True,
                         use_sma200_trendline_filter=True)
    assert cfg.enable_trendline_signals
    assert not cfg.use_trend_filter
    assert cfg.use_sma200_trendline_filter
