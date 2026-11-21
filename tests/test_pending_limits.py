from app.pending_limits import (
    PendingLimit, PendingLimitBook, PendingSetup, PendingStatus,
    make_two_limit_prices,
)
from app.storage import SQLiteStore
import pytest


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


def test_confirmation_candle_cannot_fill_newly_created_limits():
    book = PendingLimitBook()
    candidate = PendingSetup(
        "XRP/USDT", "BUY", created_at=900_000, expires_after_bars=8,
        invalidation=90.0, stop=89.0, ob_top=105.0, ob_bottom=95.0,
        limits=[PendingLimit(1, 103.0), PendingLimit(2, 98.0)],
    )
    book.add(candidate)
    # Timestamp equals the signal candle close; even if its range touched
    # the proposed levels, orders become eligible only on a later candle.
    assert book.process_candle("XRP/USDT", 900_000, 104.0, 97.0, 101.0) == []
    assert all(leg.status is PendingStatus.PENDING for leg in candidate.limits)
    events = book.process_candle("XRP/USDT", 1_800_000, 104.0, 102.0, 103.0)
    assert [(event.leg_number, event.status) for event in events] == [(1, "FILLED")]


def test_structural_wick_invalidation_precedes_any_limit_touch():
    book = PendingLimitBook()
    book.add(setup())
    events = book.process_candle("BTC/USDT", 900_000, 105.0, 97.5, 103.0)
    assert [(event.leg_number, event.status) for event in events] == [
        (1, "CANCELLED"), (2, "CANCELLED")
    ]


def test_manual_cancel_only_cancels_still_pending_leg():
    book = PendingLimitBook()
    book.add(setup())
    book.process_candle("BTC/USDT", 900_000, 105, 103, 104)
    events = book.cancel("BTC/USDT", 1_000_000, "لغو دستی")
    assert [(e.leg_number, e.status) for e in events] == [(2, "CANCELLED")]


def test_completed_setup_keeps_advancing_its_candle_cursor():
    book = PendingLimitBook()
    candidate = setup()
    book.add(candidate)
    book.process_candle("BTC/USDT", 900_000, 105, 103, 104)
    book.process_candle("BTC/USDT", 1_800_000, 102, 100, 101)
    assert candidate.active is False
    assert candidate.last_processed_bar == 1_800_000
    assert book.process_candle("BTC/USDT", 2_700_000, 105, 103, 104) == []
    assert candidate.last_processed_bar == 2_700_000


def test_active_setup_duplicate_is_rejected_but_finished_setup_can_be_replaced():
    book = PendingLimitBook()
    book.add(setup())
    with pytest.raises(ValueError, match="active pending setup"):
        book.add(setup())
    book.cancel("BTC/USDT", 900_000, "test complete")
    replacement = setup()
    replacement.created_at = 1_000_000
    book.add(replacement)
    assert book.setups["BTC/USDT"] is replacement


def test_pending_setup_persists_leg_states_and_replay_cursor_across_restart(tmp_path):
    store = SQLiteStore(str(tmp_path / "pending-restart.sqlite3"))
    original = PendingLimitBook()
    candidate = setup()
    original.add(candidate)
    original.process_candle("BTC/USDT", 900_000, 105, 103, 104)
    store.save_pending_setups(original)

    restored = store.load_pending_setups()
    resumed = restored.setups["BTC/USDT"]
    assert resumed.last_processed_bar == 900_000
    assert resumed.limits[0].status is PendingStatus.FILLED
    assert resumed.limits[0].filled_at == 900_000
    assert resumed.limits[1].status is PendingStatus.PENDING

    # Replaying the already processed candle after restart must not duplicate a fill.
    assert restored.process_candle("BTC/USDT", 900_000, 105, 103, 104) == []
    events = restored.process_candle("BTC/USDT", 1_800_000, 102, 100, 101)
    assert [(event.leg_number, event.status) for event in events] == [(2, "FILLED")]
    store.save_pending_setups(restored)
    final_state = store.load_pending_setups().setups["BTC/USDT"]
    assert final_state.last_processed_bar == 1_800_000
    assert all(leg.status is PendingStatus.FILLED for leg in final_state.limits)


def test_open_demo_position_blocks_any_new_setup_for_same_symbol():
    book = PendingLimitBook()
    assert book.can_start_setup("BTC/USDT", position_open=False, created_at=1_000_000)

    # Even if the previous setup has finished, an open position blocks re-entry.
    candidate = setup()
    book.add(candidate)
    book.cancel("BTC/USDT", 900_000, "previous setup finished")
    assert not book.can_start_setup("BTC/USDT", position_open=True, created_at=2_000_000)


def test_setup_guard_allows_new_timestamp_only_after_position_is_closed():
    book = PendingLimitBook()
    candidate = setup()
    book.add(candidate)
    book.cancel("BTC/USDT", 900_000, "previous setup finished")

    assert not book.can_start_setup("BTC/USDT", position_open=False, created_at=candidate.created_at)
    assert book.can_start_setup("BTC/USDT", position_open=False, created_at=2_000_000)
