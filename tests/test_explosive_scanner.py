from app.explosive_scanner import (
    _book_ratio, _closed_candles, _compression, _trade_buy_ratio,
)


def test_trade_flow_uses_public_aggressor_side_volumes():
    trades = [
        {"type": "buy", "volume": "6"},
        {"type": "sell", "volume": "4"},
        {"type": "unknown", "volume": "10"},
    ]
    assert _trade_buy_ratio(trades) == 0.6


def test_orderbook_imbalance_uses_top_levels():
    book = {"bids": [["100", "8"], ["99", "2"]], "asks": [["101", "5"]]}
    assert _book_ratio(book) == 2.0


def test_closed_candles_drop_unclosed_and_malformed_rows():
    now = 10_000_000
    rows = [
        [now - 15 * 60_000, 10, 12, 9, 11, 100],
        [now - 5 * 60_000, 11, 13, 10, 12, 100],
        [now - 30 * 60_000, 10, 9, 8, 9, 10],
    ]
    bars = _closed_candles(rows, "15m", now)
    assert len(bars) == 1
    assert bars[0].close == 11


def test_compression_requires_recent_range_contraction():
    class Bar:
        def __init__(self, high, low):
            self.high, self.low = high, low

    expanding = [Bar(110, 90) for _ in range(22)] + [Bar(115, 85) for _ in range(8)]
    contracting = [Bar(110, 90) for _ in range(22)] + [Bar(103, 97) for _ in range(8)]
    assert not _compression(expanding)
    assert _compression(contracting)
