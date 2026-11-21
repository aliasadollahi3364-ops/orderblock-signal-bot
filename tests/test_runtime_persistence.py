import pytest

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


def test_notification_outbox_is_atomic_deduplicated_and_survives_restart(tmp_path):
    store = SQLiteStore(str(tmp_path / "notification-outbox.sqlite3"))
    account = DemoAccount(fee_rate=0)
    book = PendingLimitBook()
    setup = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=900_000,
        expires_after_bars=8, invalidation=99, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[PendingLimit(1, 104), PendingLimit(2, 101)],
    )
    book.add(setup)
    store.save_runtime_state(account, [], book)

    # The first leg is already filled; only the deeper second leg remains cancellable.
    setup.limits[0].status = PendingStatus.FILLED
    setup.limits[0].filled_at = 1_800_000
    account.add_limit_fill(
        "BTC/USDT", Side.LONG, 104, 98,
        opened_at="1970-01-01T00:30:00+00:00", leg_number=1,
    )
    fill_event = {
        "symbol": "BTC/USDT", "side": "LONG", "type": "LIMIT_FILLED",
        "leg": 1, "price": 104, "time": "1970-01-01T00:30:00+00:00",
        "average_entry": 104, "margin_allocated": 5,
        "tp1": 110, "tp2": 116, "tp3": 122, "realized_pnl": 0.0,
    }
    events = [fill_event]
    store.save_runtime_state(account, events, book)

    queued = store.load_notification_outbox()
    assert len(queued) == 1
    assert queued[0]["kind"] == "trade"
    assert queued[0]["event"]["type"] == "LIMIT_FILLED"

    # Saving the same full event history again must not enqueue it twice.
    store.save_runtime_state(account, events, book)
    assert len(store.load_notification_outbox()) == 1

    # Cancellation transition is inserted in the same snapshot as the setup.
    cancelled = book.cancel("BTC/USDT", 2_700_000, "هدف سود فعال شد")
    assert len(cancelled) == 1
    store.save_runtime_state(account, events, book)
    queued = store.load_notification_outbox()
    assert len(queued) == 2
    assert [item["event"]["type"] for item in queued] == [
        "LIMIT_FILLED", "LIMIT_CANCELLED",
    ]

    # Delivery state is durable. Once acknowledged, it is not offered again.
    fill_id = next(item["event_id"] for item in queued if item["event"]["type"] == "LIMIT_FILLED")
    store.mark_notification_delivered(fill_id, "1970-01-01T00:31:00+00:00")
    pending = store.load_notification_outbox()
    assert len(pending) == 1
    assert pending[0]["event"]["type"] == "LIMIT_CANCELLED"

    # A fresh store instance sees the same undelivered item after process restart.
    restarted = SQLiteStore(str(tmp_path / "notification-outbox.sqlite3"))
    recovered = restarted.load_notification_outbox()
    assert [item["event"]["type"] for item in recovered] == ["LIMIT_CANCELLED"]


def test_outbox_migration_does_not_replay_existing_trade_history(tmp_path):
    store = SQLiteStore(str(tmp_path / "outbox-migration.sqlite3"))
    account = DemoAccount(fee_rate=0)
    events = [{
        "symbol": "ETH/USDT", "side": "LONG", "type": "TP1",
        "price": 120, "time": "2026-01-01T00:00:00+00:00",
        "realized_pnl": 1.25,
    }]
    # Simulate an installation that already had event history before outbox.
    store.save_state(account, events)
    store.save_runtime_state(account, events, PendingLimitBook())
    assert store.load_notification_outbox() == []


def _assert_closed_trade_snapshot_survives_restart(tmp_path, exit_kind):
    store = SQLiteStore(str(tmp_path / f"{exit_kind.lower()}-restart.sqlite3"))
    account = DemoAccount(fee_rate=0)
    book = PendingLimitBook()
    setup = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=900_000,
        expires_after_bars=8, invalidation=99, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[
            PendingLimit(1, 104, PendingStatus.FILLED, filled_at=1_800_000),
            PendingLimit(2, 101),
        ],
    )
    book.add(setup)
    position = account.add_limit_fill(
        "BTC/USDT", Side.LONG, 104, 98,
        opened_at="1970-01-01T00:30:00+00:00", leg_number=1,
    )
    fill_event = {
        "symbol": "BTC/USDT", "side": "LONG", "type": "LIMIT_FILLED",
        "leg": 1, "price": 104, "time": "1970-01-01T00:30:00+00:00",
        "average_entry": 104, "margin_allocated": 5,
        "tp1": position.tp1, "tp2": position.tp2, "tp3": position.tp3,
        "realized_pnl": 0.0,
    }
    events = [fill_event]
    store.save_runtime_state(account, events, book)
    fill_id = store.load_notification_outbox()[0]["event_id"]
    store.mark_notification_delivered(fill_id, "1970-01-01T00:31:00+00:00")

    if exit_kind == "STOP":
        candle = {"high": 105, "low": 97}
    elif exit_kind == "TP3":
        candle = {"high": position.tp3 + 1, "low": position.tp1 + 1}
    else:
        raise AssertionError(f"Unexpected exit kind: {exit_kind}")

    exit_events = account.process_candle(
        high=candle["high"], low=candle["low"], symbol="BTC/USDT",
    )
    expected_exit_types = ["STOP"] if exit_kind == "STOP" else ["TP1", "TP2", "TP3"]
    assert [event["type"] for event in exit_events] == expected_exit_types
    assert position.closed is True
    assert position.quantity_remaining == 0
    assert account.margin_used == 0

    for exit_event in exit_events:
        events.append({
            "symbol": "BTC/USDT", "side": "LONG", **exit_event,
            "time": "1970-01-01T00:45:00+00:00",
            "realized_pnl": position.realized_pnl,
        })
    cancelled = book.cancel(
        "BTC/USDT", 2_700_000, "پوزیشن بسته شد؛ لیمیت باقیمانده لغو شد",
    )
    assert [item.leg_number for item in cancelled] == [2]

    # Closed position, final ledger, pending cancellation and outbox enter one transaction.
    store.save_runtime_state(account, events, book)

    restarted_store = SQLiteStore(str(tmp_path / f"{exit_kind.lower()}-restart.sqlite3"))
    restored_account, restored_events = restarted_store.load_state(DemoAccount())
    restored_book = restarted_store.load_pending_setups()
    restored = restored_account.positions["BTC/USDT"]

    assert restored.closed is True
    assert restored.quantity_remaining == 0
    assert restored.margin_allocated == pytest.approx(5)
    assert restored_account.margin_used == pytest.approx(0)
    assert restored_account.balance == pytest.approx(account.balance)
    assert restored.realized_pnl == pytest.approx(position.realized_pnl)
    assert [event["type"] for event in restored_events if event["type"] in {"TP1", "TP2", "TP3", "STOP"}][-len(expected_exit_types):] == expected_exit_types
    assert restored_events[-1]["realized_pnl"] == pytest.approx(position.realized_pnl)
    assert restored_book.setups["BTC/USDT"].limits[0].status is PendingStatus.FILLED
    assert restored_book.setups["BTC/USDT"].limits[1].status is PendingStatus.CANCELLED

    pending = restarted_store.load_notification_outbox()
    assert {item["event"]["type"] for item in pending} == set(expected_exit_types) | {"LIMIT_CANCELLED"}
    assert len(pending) == len(expected_exit_types) + 1

    # Re-saving the same closed snapshot after restart cannot duplicate exit alerts.
    restarted_store.save_runtime_state(restored_account, restored_events, restored_book)
    assert len(restarted_store.load_notification_outbox()) == len(expected_exit_types) + 1


def test_stop_close_and_cancel_are_restored_atomically_after_restart(tmp_path):
    _assert_closed_trade_snapshot_survives_restart(tmp_path, "STOP")


def test_tp3_close_and_cancel_are_restored_atomically_after_restart(tmp_path):
    _assert_closed_trade_snapshot_survives_restart(tmp_path, "TP3")
