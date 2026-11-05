from app.scanner import _active_ob_distance, _to_candles
from app.strategy import Candle, OrderBlock, OrderBlockStrategy


def test_to_candles_sorts_and_deduplicates_exchange_rows():
    bars = _to_candles([
        [2000, 11, 12, 10, 11.5, 3],
        [1000, 10, 11, 9, 10.5, 2],
        [2000, 11, 13, 10, 12, 4],
    ])
    assert [bar.timestamp for bar in bars] == [1000, 2000]
    assert bars[-1].close == 12
    assert isinstance(bars[0], Candle)


def test_active_ob_distance_is_zero_inside_zone():
    engine = OrderBlockStrategy("BTC/USDT")
    engine.order_blocks.append(OrderBlock(1, 105, 100, 1))
    assert _active_ob_distance(engine, 102) == 0


def test_active_ob_distance_ignores_inactive_or_used_zones():
    engine = OrderBlockStrategy("BTC/USDT")
    engine.order_blocks.append(OrderBlock(1, 105, 100, 1, active=False))
    engine.order_blocks.append(OrderBlock(-1, 110, 108, 2, used=True))
    assert _active_ob_distance(engine, 102) is None
