from app.demo import DemoAccount, Side
from app.pending_limits import PendingLimit, PendingLimitBook, PendingSetup, PendingStatus
from app.runtime import BotRuntime
from app.storage import SQLiteStore


def test_limit_fill_and_demo_position_are_committed_as_one_restart_snapshot(tmp_path):
    store = SQLiteStore(str(tmp_path / "atomic-runtime.sqlite3"))
    account = DemoAccount(fee_rate=0)
    book = PendingLimitBook()
    setup = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=900_000,
        expires_after_bars=8, invalidation=99, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[PendingLimit(1, 104), PendingLimit(2, 101)],
    )
    book.add(setup)
    app_runtime = BotRuntime(account=account, storage=store, pending_limits=book)
    store.save_pending_setups(book)  # Signal creation is durably stored before any candle can fill it.

    events = book.process_candle("BTC/USDT", 1_800_000, 105, 103, 104)
    assert [(event.leg_number, event.status) for event in events] == [(1, "FILLED")]

    # Scanner's first persistence call occurs before it books the demo fill.
    # It must defer rather than commit a FILLED leg without its matching position.
    app_runtime.persist_pending_limits()
    restored_book = store.load_pending_setups()
    assert restored_book.setups["BTC/USDT"].limits[0].status is PendingStatus.PENDING

    account.add_limit_fill(
        "BTC/USDT", Side.LONG, 104, 98,
        opened_at="1970-01-01T00:30:00+00:00", leg_number=1,
    )
    app_runtime.closed_trade_events.append({
        "symbol": "BTC/USDT", "type": "LIMIT_FILLED", "leg": 1, "price": 104,
    })
    app_runtime.persist_demo_state()

    restored_account, restored_events = store.load_state(DemoAccount())
    restored_book = store.load_pending_setups()
    restored_position = restored_account.positions["BTC/USDT"]
    assert restored_book.setups["BTC/USDT"].limits[0].status is PendingStatus.FILLED
    assert restored_book.setups["BTC/USDT"].limits[0].filled_at == 1_800_000
    assert len(restored_position.legs) == 1
    assert restored_position.legs[0]["number"] == 1
    assert restored_position.legs[0]["entry"] == 104
    assert restored_events[-1]["type"] == "LIMIT_FILLED"


def test_two_fills_on_same_candle_are_not_half_committed(tmp_path):
    store = SQLiteStore(str(tmp_path / "atomic-two-fills.sqlite3"))
    account = DemoAccount(fee_rate=0)
    book = PendingLimitBook()
    setup = PendingSetup(
        symbol="ETH/USDT", side="BUY", created_at=900_000,
        expires_after_bars=8, invalidation=99, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[PendingLimit(1, 104), PendingLimit(2, 101)],
    )
    book.add(setup)
    app_runtime = BotRuntime(account=account, storage=store, pending_limits=book)
    store.save_pending_setups(book)

    events = book.process_candle("ETH/USDT", 1_800_000, 105, 100, 103)
    assert [event.leg_number for event in events] == [1, 2]

    account.add_limit_fill(
        "ETH/USDT", Side.LONG, 104, 98,
        opened_at="1970-01-01T00:30:00+00:00", leg_number=1,
    )
    # The second leg is already marked FILLED in memory but is not booked yet.
    app_runtime.persist_demo_state()
    restored_account, _ = store.load_state(DemoAccount())
    restored_book = store.load_pending_setups()
    assert "ETH/USDT" not in restored_account.positions
    assert restored_book.setups["ETH/USDT"].limits[0].status is PendingStatus.PENDING

    account.add_limit_fill(
        "ETH/USDT", Side.LONG, 101, 98,
        opened_at="1970-01-01T00:45:00+00:00", leg_number=2,
    )
    app_runtime.persist_demo_state()

    restored_account, _ = store.load_state(DemoAccount())
    restored_book = store.load_pending_setups()
    assert len(restored_account.positions["ETH/USDT"].legs) == 2
    assert [leg.status for leg in restored_book.setups["ETH/USDT"].limits] == [
        PendingStatus.FILLED, PendingStatus.FILLED,
    ]
