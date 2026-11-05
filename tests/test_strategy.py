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
