import json
import sqlite3

from app.storage import SQLiteStore
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


def test_nearest_touched_ob_direction_uses_absolute_distance_and_skips_inactive_zones():
    from app.strategy import OrderBlock

    engine = OrderBlockStrategy("BTC/USDT")
    engine.order_blocks.extend([
        OrderBlock(1, 100.0, 95.0, 1, used=True),
        OrderBlock(-1, 112.0, 108.0, 2),
        OrderBlock(-1, 104.0, 101.0, 3, active=False),
    ])
    candle = bar(100, 100.0, 110.0, 99.0, 102.0)
    # The bullish zone is 2 points away; the bearish zone is 6 points away.
    assert engine._nearest_touched_ob_direction(candle) == 1


def test_trendline_signal_configuration_is_independent_from_ob_trend_filter():
    cfg = StrategyConfig(use_trend_filter=False, enable_trendline_signals=True,
                         use_sma200_trendline_filter=True)
    assert cfg.enable_trendline_signals
    assert not cfg.use_trend_filter
    assert cfg.use_sma200_trendline_filter



def test_saved_strategy_settings_cannot_disable_required_trendline_pullback(tmp_path):
    path = str(tmp_path / "trendline-pullback.sqlite3")
    store = SQLiteStore(path)
    cfg = StrategyConfig(require_trendline_pullback=False)

    store.save_strategy_config(cfg)
    loaded = store.load_strategy_config()

    assert loaded.require_trendline_pullback is True
    assert cfg.require_trendline_pullback is False  # saving does not mutate caller state


def test_legacy_saved_strategy_settings_reset_to_current_pine_defaults(tmp_path):
    path = str(tmp_path / "legacy-settings.sqlite3")
    store = SQLiteStore(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO scanner_settings(key, value) VALUES ('strategy_config', ?)",
            (json.dumps({"pivot4": 3, "sl_buffer_atr": 0.15,
                         "use_sma200_filter": False}),),
        )
    cfg = store.load_strategy_config()
    assert cfg.sl_buffer_atr == 2.0
    assert cfg.use_sma200_filter is True
    assert cfg.enable_trendline_signals is True
    assert cfg.use_trend_filter is True



def test_trendline_break_waits_for_a_later_pullback_and_confirmation():
    from app.strategy import OrderBlock

    cfg = StrategyConfig(
        atr_len=1,
        use_trend_filter=False,
        use_sma200_filter=False,
        enable_trendline_signals=True,
        use_sma200_trendline_filter=False,
        require_trendline_pullback=False,  # legacy saved value must not bypass Pine's pullback rule
    )
    engine = OrderBlockStrategy("BTC/USDT", cfg)
    engine.candles15.append(bar(1, 104.0, 105.0, 103.0, 104.0))
    engine._last_15m_timestamp = 1
    engine._tr15.append(2.0)
    engine.prev_ph15, engine.prev_ph15_bar = 110.0, -1
    engine.last_ph15, engine.last_ph15_bar = 105.0, 0
    engine.last_pl15 = 90.0
    engine.order_blocks.append(OrderBlock(1, 108.0, 100.0, 10))

    breakout = engine.on_15m_close(bar(2, 105.0, 107.0, 103.0, 106.0))
    assert breakout is None  # breakout alone is not an entry
    warning = engine.last_early_warning
    assert warning is not None
    assert warning.side == "BUY"
    assert warning.timestamp == 2
    assert warning.trendline_level == 105.0
    assert warning.ob_top == 108.0
    assert warning.ob_bottom == 100.0

    pullback = engine.on_15m_close(bar(3, 100.0, 101.0, 99.8, 100.8))
    assert engine.last_early_warning is None  # one warning, only on the breakout candle
    assert pullback is not None
    assert pullback.method == "TRENDLINE"
    assert pullback.side == "BUY"
    assert pullback.stop < pullback.entry
    assert pullback.ob_time == 10
    assert pullback.ob_top == 108.0
    assert pullback.ob_bottom == 100.0

def test_unqualified_raw_bos_does_not_change_4h_trend():
    cfg = StrategyConfig(pivot4=1, body_len=2, ob_search=3, ob_atr_len=1)
    engine = OrderBlockStrategy("BTC/USDT", cfg)

    engine.on_4h_close(bar(1, 10, 11, 8, 9))
    engine.on_4h_close(bar(2, 9, 12, 8.5, 10))
    engine.on_4h_close(bar(3, 10, 11, 9, 10.5))  # confirms pivot high at 12
    created = engine.on_4h_close(bar(4, 12.0, 12.2, 11.9, 12.1))  # weak raw break

    assert created == []
    assert engine.trend4h == 0


def test_trendline_entry_requires_touching_an_active_bullish_order_block():
    cfg = StrategyConfig(
        atr_len=1, use_trend_filter=False, use_sma200_filter=False,
        enable_trendline_signals=True, use_sma200_trendline_filter=False,
        require_trendline_pullback=True,
    )
    engine = OrderBlockStrategy("BTC/USDT", cfg)
    engine.candles15.append(bar(1, 104.0, 105.0, 103.0, 104.0))
    engine._last_15m_timestamp = 1
    engine._tr15.append(2.0)
    engine.prev_ph15, engine.prev_ph15_bar = 110.0, -1
    engine.last_ph15, engine.last_ph15_bar = 105.0, 0
    engine.last_pl15 = 90.0

    assert engine.on_15m_close(bar(2, 105.0, 107.0, 103.0, 106.0)) is None
    assert engine.on_15m_close(bar(3, 100.0, 101.0, 99.8, 100.8)) is None


def test_trendline_long_is_blocked_when_touched_order_block_is_bearish():
    from app.strategy import OrderBlock

    cfg = StrategyConfig(
        atr_len=1, use_trend_filter=False, use_sma200_filter=False,
        enable_trendline_signals=True, use_sma200_trendline_filter=False,
        require_trendline_pullback=True,
    )
    engine = OrderBlockStrategy("BTC/USDT", cfg)
    engine.candles15.append(bar(1, 104.0, 105.0, 103.0, 104.0))
    engine._last_15m_timestamp = 1
    engine._tr15.append(2.0)
    engine.prev_ph15, engine.prev_ph15_bar = 110.0, -1
    engine.last_ph15, engine.last_ph15_bar = 105.0, 0
    engine.last_pl15 = 90.0
    engine.order_blocks.append(OrderBlock(-1, 108.0, 100.0, 10))

    assert engine.on_15m_close(bar(2, 105.0, 107.0, 103.0, 106.0)) is None
    assert engine.on_15m_close(bar(3, 100.0, 101.0, 99.8, 100.8)) is None



def test_rsi_bullish_divergence_is_detected_and_does_not_gate_signals():
    from app.strategy import OrderBlock

    cfg = StrategyConfig(
        pivot15=1, atr_len=1, rsi_length=2, enable_rsi_divergence=True,
        use_trend_filter=False, use_sma200_filter=False,
        use_choch_filter=False, use_retest_filter=False,
        use_confirm_strength=False, use_sma200_trendline_filter=False,
    )
    engine = OrderBlockStrategy("BTC/USDT", cfg)
    # A newly confirmed lower price low with a higher RSI low is bullish divergence.
    engine.candles15.extend([
        bar(1, 11, 12, 10, 11),
        bar(2, 10, 11, 9, 10),
    ])
    engine._rsi_values.extend([35.0, 42.0])
    engine.last_pl15 = 10.0
    engine._previous_rsi_pivot_low = 35.0

    engine.on_15m_close(bar(3, 10, 11, 9.5, 10.5))

    assert engine._bullish_divergence_bar == 2
    state, value = engine._signal_rsi_context("BUY", 2)
    assert state == "BULLISH"
    assert value is not None


def test_no_rsi_divergence_remains_neutral_not_blocked():
    cfg = StrategyConfig(enable_rsi_divergence=True, rsi_length=2)
    engine = OrderBlockStrategy("BTC/USDT", cfg)
    engine.candles15.extend([bar(1, 10, 11, 9, 10), bar(2, 10, 11, 9, 10)])
    engine._rsi_values.extend([None, None])

    state, value = engine._signal_rsi_context("BUY", 1)

    assert state == "NONE"
    assert value is None


def test_rsi_divergence_can_be_disabled_without_changing_entry_logic():
    cfg = StrategyConfig(enable_rsi_divergence=False)
    engine = OrderBlockStrategy("BTC/USDT", cfg)
    state, value = engine._signal_rsi_context("SELL", 0)

    assert state == "DISABLED"
    assert value is None


def test_order_block_warning_waits_for_choch_and_confirmation_candle():
    from app.strategy import OrderBlock

    cfg = StrategyConfig(
        pivot15=1, atr_len=1, require_ob_touch=True,
        use_touch_freshness=True, use_choch_filter=True,
        use_choch_strength=False, use_retest_filter=False,
        use_confirm_candle=True, use_confirm_strength=False,
        use_ob_boundary_sl=True, use_trend_filter=False,
        use_sma200_filter=False, min_tick=0.001,
    )
    engine = OrderBlockStrategy("BTC/USDT", cfg)
    engine.last_pl15 = 95.0
    ob = OrderBlock(
        direction=1, top=100.0, bottom=98.0, origin_time=123,
        has_choch=True, choch_level=99.0, choch_bar=-1,
    )
    engine.order_blocks.append(ob)

    signal = engine.on_15m_close(bar(1000, 99.5, 101.5, 99.0, 101.0))

    assert signal is not None
    assert signal.side == "BUY"
    assert engine.last_early_warning is not None
    assert engine.last_early_warning.kind == "ORDER_BLOCK_CONFIRMATION"
    assert engine.last_early_warning.timestamp == 1000
    assert engine.last_early_warning.ob_top == 100.0
    assert engine.last_early_warning.ob_bottom == 98.0
