from app.gold_market import GoldStore, aggregate_candles
from app.strategy import Candle


def test_aggregate_candles_builds_only_closed_bars():
    width = 5 * 60_000
    ticks = [
        (0, 100.0), (20_000, 101.0), (60_000, 99.0),
        (width + 10_000, 102.0),
    ]
    bars = aggregate_candles(ticks, width, now_ms=width + 1)
    assert len(bars) == 1
    assert bars[0] == Candle(0, 100.0, 101.0, 99.0, 99.0)


def test_gold_store_persists_samples_and_candles(tmp_path):
    store = GoldStore(str(tmp_path / "gold.sqlite3"))
    assert store.add_tick(1_000, 25_000_000, 270_000, 3_500.0)
    assert not store.add_tick(1_000, 25_000_001, 270_000, 3_500.0)
    assert store.latest_tick() == (1_000, 25_000_000.0, 270_000.0, 3_500.0)
    candle = Candle(0, 25_000_000, 25_100_000, 24_900_000, 25_050_000)
    store.save_candle("5m", candle)
    assert store.candles("5m") == [candle]


def test_gold_store_deduplicates_signal_events(tmp_path):
    from app.strategy import Signal

    store = GoldStore(str(tmp_path / "gold-signals.sqlite3"))
    signal = Signal(
        symbol="GOLD_18K_IRR", side="BUY", timestamp=900_000,
        entry=25_000_000, stop=24_900_000, atr=100_000,
        ob_time=0, ob_top=25_100_000, ob_bottom=24_900_000,
        trend4h=1, sma200=24_800_000, method="ORDER_BLOCK",
    )
    assert store.claim_signal(signal)
    assert not store.claim_signal(signal)
