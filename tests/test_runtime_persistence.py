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


def test_second_limit_fill_after_restart_restores_one_aggregated_position_and_alerts(tmp_path):
    db_path = str(tmp_path / "second-leg-restart.sqlite3")
    store = SQLiteStore(db_path)
    account = DemoAccount(fee_rate=0)
    book = PendingLimitBook()
    setup = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=900_000,
        expires_after_bars=8, invalidation=99, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[PendingLimit(1, 104), PendingLimit(2, 101)],
    )
    book.add(setup)
    runtime_before = BotRuntime(account=account, storage=store, pending_limits=book)
    store.save_pending_setups(book)

    first_events = book.process_candle("BTC/USDT", 1_800_000, 105, 103, 104)
    assert [(e.leg_number, e.status) for e in first_events] == [(1, "FILLED")]
    first_position = account.add_limit_fill(
        "BTC/USDT", Side.LONG, 104, 98,
        opened_at="1970-01-01T00:30:00+00:00", leg_number=1,
    )
    first_alert = {
        "symbol": "BTC/USDT", "side": "LONG", "type": "LIMIT_FILLED",
        "leg": 1, "price": 104, "time": "1970-01-01T00:30:00+00:00",
        "average_entry": first_position.entry, "margin_allocated": 5,
        "tp1": first_position.tp1, "tp2": first_position.tp2,
        "tp3": first_position.tp3, "realized_pnl": first_position.realized_pnl,
    }
    runtime_before.closed_trade_events.append(first_alert)
    runtime_before.persist_demo_state()

    # Simulate process death: recover the account, pending book, cursor and alert queue.
    restarted_store = SQLiteStore(db_path)
    restored_account, restored_history = restarted_store.load_state(DemoAccount())
    restored_book = restarted_store.load_pending_setups()
    restored_runtime = BotRuntime(
        account=restored_account, storage=restarted_store,
        pending_limits=restored_book, closed_trade_events=restored_history,
    )
    restored_setup = restored_book.setups["BTC/USDT"]
    assert restored_setup.last_processed_bar == 1_800_000
    assert restored_setup.limits[0].status is PendingStatus.FILLED
    assert restored_setup.limits[1].status is PendingStatus.PENDING
    assert restored_account.margin_used == pytest.approx(5)
    assert len(restored_account.positions["BTC/USDT"].legs) == 1

    # The next closed candle fills only the deeper second limit after restart.
    second_events = restored_book.process_candle(
        "BTC/USDT", 2_700_000, 102, 100.5, 101.5,
    )
    assert [(e.leg_number, e.status) for e in second_events] == [(2, "FILLED")]
    second_position = restored_account.add_limit_fill(
        "BTC/USDT", Side.LONG, 101, 98,
        opened_at="1970-01-01T00:45:00+00:00", leg_number=2,
    )
    second_alert = {
        "symbol": "BTC/USDT", "side": "LONG", "type": "LIMIT_FILLED",
        "leg": 2, "price": 101, "time": "1970-01-01T00:45:00+00:00",
        "average_entry": second_position.entry, "margin_allocated": 10,
        "tp1": second_position.tp1, "tp2": second_position.tp2,
        "tp3": second_position.tp3, "realized_pnl": second_position.realized_pnl,
    }
    restored_runtime.closed_trade_events.append(second_alert)
    restored_runtime.persist_demo_state()

    # A second restart must recover both legs as one position with the weighted entry.
    final_store = SQLiteStore(db_path)
    final_account, final_history = final_store.load_state(DemoAccount())
    final_book = final_store.load_pending_setups()
    final_position = final_account.positions["BTC/USDT"]
    expected_entry = ((25 / 104) * 104 + (25 / 101) * 101) / ((25 / 104) + (25 / 101))
    assert len(final_account.positions) == 1
    assert len(final_position.legs) == 2
    assert [leg["number"] for leg in final_position.legs] == [1, 2]
    assert final_position.entry == pytest.approx(expected_entry)
    assert final_position.margin_allocated == pytest.approx(10)
    assert final_account.margin_used == pytest.approx(10)
    assert final_account.free_margin == pytest.approx(90)
    assert final_position.closed is False
    assert [leg.status for leg in final_book.setups["BTC/USDT"].limits] == [
        PendingStatus.FILLED, PendingStatus.FILLED,
    ]
    assert final_book.setups["BTC/USDT"].last_processed_bar == 2_700_000
    assert [event["leg"] for event in final_history if event.get("type") == "LIMIT_FILLED"] == [1, 2]
    queued = final_store.load_notification_outbox()
    assert [item["event"]["leg"] for item in queued if item["event"]["type"] == "LIMIT_FILLED"] == [1, 2]

    # Replaying the already-processed candle or persisting the same snapshot is idempotent.
    assert final_book.process_candle("BTC/USDT", 2_700_000, 102, 100.5, 101.5) == []
    restored_runtime_again = BotRuntime(
        account=final_account, storage=final_store,
        pending_limits=final_book, closed_trade_events=final_history,
    )
    restored_runtime_again.persist_demo_state()
    assert len(final_store.load_notification_outbox()) == 2


def _assert_two_filled_legs_exit_survives_restart(tmp_path, exit_kind):
    db_path = str(tmp_path / f"two-leg-{exit_kind.lower()}.sqlite3")
    store = SQLiteStore(db_path)
    account = DemoAccount(fee_rate=0)
    book = PendingLimitBook()
    setup = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=900_000,
        expires_after_bars=8, invalidation=99, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[
            PendingLimit(1, 104, PendingStatus.FILLED, filled_at=1_800_000),
            PendingLimit(2, 101, PendingStatus.FILLED, filled_at=2_700_000),
        ],
        last_processed_bar=2_700_000,
    )
    book.add(setup)
    position = account.add_limit_fill(
        "BTC/USDT", Side.LONG, 104, 98,
        opened_at="1970-01-01T00:30:00+00:00", leg_number=1,
    )
    position = account.add_limit_fill(
        "BTC/USDT", Side.LONG, 101, 98,
        opened_at="1970-01-01T00:45:00+00:00", leg_number=2,
    )
    events = [
        {
            "symbol": "BTC/USDT", "side": "LONG", "type": "LIMIT_FILLED",
            "leg": 1, "price": 104, "time": "1970-01-01T00:30:00+00:00",
            "average_entry": 104, "margin_allocated": 5,
            "tp1": 106, "tp2": 108, "tp3": 110, "realized_pnl": 0.0,
        },
        {
            "symbol": "BTC/USDT", "side": "LONG", "type": "LIMIT_FILLED",
            "leg": 2, "price": 101, "time": "1970-01-01T00:45:00+00:00",
            "average_entry": position.entry, "margin_allocated": 10,
            "tp1": position.tp1, "tp2": position.tp2, "tp3": position.tp3,
            "realized_pnl": 0.0,
        },
    ]
    store.save_runtime_state(account, events, book)

    # Restart with both legs filled and the aggregate position fully restored.
    restarted_store = SQLiteStore(db_path)
    restored_account, restored_events = restarted_store.load_state(DemoAccount())
    restored_book = restarted_store.load_pending_setups()
    restored = restored_account.positions["BTC/USDT"]
    assert len(restored.legs) == 2
    assert restored.margin_allocated == pytest.approx(10)
    assert restored_account.margin_used == pytest.approx(10)
    assert [leg.status for leg in restored_book.setups["BTC/USDT"].limits] == [
        PendingStatus.FILLED, PendingStatus.FILLED,
    ]

    if exit_kind == "STOP":
        high, low = 105, 97
        expected = ["STOP"]
    elif exit_kind == "TP3":
        high, low = restored.tp3 + 1, restored.entry + 0.1
        expected = ["TP1", "TP2", "TP3"]
    else:
        raise AssertionError(f"Unexpected exit kind: {exit_kind}")

    exit_events = restored_account.process_candle(
        high=high, low=low, symbol="BTC/USDT",
    )
    assert [event["type"] for event in exit_events] == expected
    assert restored.closed is True
    assert restored.quantity_remaining == pytest.approx(0)
    assert restored_account.margin_used == pytest.approx(0)

    for event in exit_events:
        restored_events.append({
            "symbol": "BTC/USDT", "side": "LONG", **event,
            "time": "1970-01-01T01:00:00+00:00",
            "realized_pnl": restored.realized_pnl,
        })
    restarted_store.save_runtime_state(restored_account, restored_events, restored_book)

    final_store = SQLiteStore(db_path)
    final_account, final_events = final_store.load_state(DemoAccount())
    final_book = final_store.load_pending_setups()
    final_position = final_account.positions["BTC/USDT"]
    assert final_position.closed is True
    assert final_position.quantity_remaining == pytest.approx(0)
    assert final_position.margin_allocated == pytest.approx(10)
    assert final_account.margin_used == pytest.approx(0)
    assert final_account.balance == pytest.approx(restored_account.balance)
    assert final_position.realized_pnl == pytest.approx(restored.realized_pnl)
    assert len(final_position.legs) == 2
    assert [leg.status for leg in final_book.setups["BTC/USDT"].limits] == [
        PendingStatus.FILLED, PendingStatus.FILLED,
    ]
    assert [e["type"] for e in final_events if e["type"] in {"STOP", "TP1", "TP2", "TP3"}][-len(expected):] == expected

    outbox = final_store.load_notification_outbox()
    assert [item["event"]["type"] for item in outbox] == ["LIMIT_FILLED", "LIMIT_FILLED"] + expected
    final_store.save_runtime_state(final_account, final_events, final_book)
    assert len(final_store.load_notification_outbox()) == len(outbox)


def test_stop_after_both_limit_fills_survives_restart_atomically(tmp_path):
    _assert_two_filled_legs_exit_survives_restart(tmp_path, "STOP")


def test_tp3_after_both_limit_fills_survives_restart_atomically(tmp_path):
    _assert_two_filled_legs_exit_survives_restart(tmp_path, "TP3")
