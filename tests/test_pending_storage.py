from app.pending_limits import PendingLimit, PendingLimitBook, PendingSetup, PendingStatus
from app.storage import SQLiteStore


def test_pending_setups_restore_both_leg_states_and_expiry_metadata(tmp_path):
    store = SQLiteStore(str(tmp_path / "pending.sqlite3"))
    book = PendingLimitBook()
    setup = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=123456,
        expires_after_bars=12, invalidation=98, stop=97,
        ob_top=105, ob_bottom=99,
        limits=[PendingLimit(1, 103), PendingLimit(2, 101)],
    )
    book.add(setup)
    events = book.process_candle("BTC/USDT", 900000, 104, 102, 103)
    assert events[0].status == "FILLED"
    store.save_pending_setups(book)

    restored = store.load_pending_setups()
    saved = restored.setups["BTC/USDT"]
    assert saved.created_at == 123456
    assert saved.expires_after_bars == 12
    assert saved.last_processed_bar == 900000
    assert saved.limits[0].status is PendingStatus.FILLED
    assert saved.limits[0].filled_at == 900000
    assert saved.limits[1].status is PendingStatus.PENDING


def test_pending_setups_restore_cancelled_reason(tmp_path):
    store = SQLiteStore(str(tmp_path / "pending-cancel.sqlite3"))
    book = PendingLimitBook()
    book.add(PendingSetup(
        symbol="ETH/USDT", side="SELL", created_at=0,
        expires_after_bars=10, invalidation=112, stop=113,
        ob_top=110, ob_bottom=104,
        limits=[PendingLimit(1, 106), PendingLimit(2, 109)],
    ))
    book.cancel("ETH/USDT", 1234, "ساختار بازار بی‌اعتبار شد")
    store.save_pending_setups(book)
    restored = store.load_pending_setups()
    legs = restored.setups["ETH/USDT"].limits
    assert all(leg.status is PendingStatus.CANCELLED for leg in legs)
    assert all(leg.cancel_reason == "ساختار بازار بی‌اعتبار شد" for leg in legs)
