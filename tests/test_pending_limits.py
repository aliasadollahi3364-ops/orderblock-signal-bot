from app.pending_limits import (
    PendingLimit, PendingLimitBook, PendingSetup, PendingStatus,
    make_two_limit_prices,
)


def setup(side="BUY"):
    if side == "BUY":
        prices = [104.0, 101.0]
        return PendingSetup("BTC/USDT", side, 0, 8, 99.0, 98.0,
                            106.0, 100.0,
                            [PendingLimit(1, prices[0]), PendingLimit(2, prices[1])])
    prices = [106.0, 109.0]
    return PendingSetup("ETH/USDT", side, 0, 8, 111.0, 112.0,
                        110.0, 104.0,
                        [PendingLimit(1, prices[0]), PendingLimit(2, prices[1])])


def test_two_limit_prices_are_staggered_and_direction_aware():
    buy = make_two_limit_prices("BUY", 110, 100, 103)
    sell = make_two_limit_prices("SELL", 110, 100, 107)
    assert buy[0] > buy[1]
    assert sell[0] < sell[1]
    assert len(set(buy)) == len(set(sell)) == 2


def test_each_leg_fills_independently_only_when_its_price_is_touched():
    book = PendingLimitBook()
    book.add(setup())
    events = book.process_candle("BTC/USDT", 900_000, 105, 103, 104)
    assert [(e.leg_number, e.status, e.price) for e in events] == [(1, "FILLED", 104)]
    assert book.setups["BTC/USDT"].limits[0].status is PendingStatus.FILLED
    assert book.setups["BTC/USDT"].limits[1].status is PendingStatus.PENDING
    events = book.process_candle("BTC/USDT", 1_800_000, 102, 100, 101)
    assert [(e.leg_number, e.status) for e in events] == [(2, "FILLED")]


def test_invalidation_cancels_each_unfilled_leg_with_reason():
    book = PendingLimitBook()
    book.add(setup())
    events = book.process_candle("BTC/USDT", 900_000, 103, 98, 98.5)
    assert [(e.leg_number, e.status) for e in events] == [(1, "CANCELLED"), (2, "CANCELLED")]
    assert all("بی‌اعتبار" in e.reason for e in events)


def test_expiry_cancels_remaining_leg_but_preserves_already_filled_leg():
    book = PendingLimitBook()
    book.add(setup())
    book.process_candle("BTC/USDT", 900_000, 105, 103, 104)
    events = book.process_candle("BTC/USDT", 8 * 900_000, 105, 103, 104)
    assert [(e.leg_number, e.status) for e in events] == [(2, "CANCELLED")]
    assert "مهلت" in events[0].reason


def test_short_invalidation_cancels_both_legs():
    book = PendingLimitBook()
    book.add(setup("SELL"))
    events = book.process_candle("ETH/USDT", 900_000, 112, 106, 111.5)
    assert [(e.leg_number, e.status) for e in events] == [(1, "CANCELLED"), (2, "CANCELLED")]


def test_manual_cancel_only_cancels_still_pending_leg():
    book = PendingLimitBook()
    book.add(setup())
    book.process_candle("BTC/USDT", 900_000, 105, 103, 104)
    events = book.cancel("BTC/USDT", 1_000_000, "لغو دستی")
    assert [(e.leg_number, e.status) for e in events] == [(2, "CANCELLED")]
