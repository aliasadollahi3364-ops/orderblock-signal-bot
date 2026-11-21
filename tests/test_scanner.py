import pytest
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


def test_to_candles_skips_zero_nonfinite_and_inconsistent_ohlc_rows():
    bars = _to_candles([
        [1000, 0, 0, 0, 0, 1],
        [2000, 10, 11, 9, float("nan"), 1],
        [3000, 10, 9, 8, 10, 1],
        [4000, 10, 11, 9, 10, 1],
    ])
    assert [bar.timestamp for bar in bars] == [4000]


def test_to_candles_skips_malformed_rows_without_aborting():
    bars = _to_candles([
        [1000, 10, 11, 9, 10, 1],
        [2000, "bad", 11, 9, 10, 1],
        [3000, 10, 11, 9, 10, 1],
    ])
    assert [bar.timestamp for bar in bars] == [1000, 3000]


def test_active_ob_distance_is_zero_inside_zone():
    engine = OrderBlockStrategy("BTC/USDT")
    engine.order_blocks.append(OrderBlock(1, 105, 100, 1))
    assert _active_ob_distance(engine, 102) == 0


def test_active_ob_distance_ignores_inactive_or_used_zones():
    engine = OrderBlockStrategy("BTC/USDT")
    engine.order_blocks.append(OrderBlock(1, 105, 100, 1, active=False))
    engine.order_blocks.append(OrderBlock(-1, 110, 108, 2, used=True))
    assert _active_ob_distance(engine, 102) is None


def test_scanner_does_not_discover_or_scan_any_exchange_symbols_automatically(monkeypatch):
    from app.scanner import AutomaticScanner
    from app.runtime import runtime

    class ManualOnlyMarketData:
        def resolve_manual_symbol(self, requested):
            raise AssertionError("No symbol should be resolved when the manual list is empty.")

        def fetch_usdt_perpetuals(self, limit=500):
            raise AssertionError("Automatic market discovery must never be called.")

    monkeypatch.setattr(runtime, "manual_symbols", [])
    monkeypatch.setattr(runtime, "selected_symbols", [])
    scanner = AutomaticScanner(ManualOnlyMarketData())
    summary = scanner.scan_once()

    assert summary.markets_seen == 0
    assert summary.signals == 0
    assert runtime.selected_symbols == []



def test_scanner_replays_missed_candles_and_fills_both_limit_legs(monkeypatch):
    from app.demo import DemoAccount
    from app.pending_limits import PendingLimit, PendingLimitBook, PendingSetup
    from app.runtime import runtime
    from app.scanner import AutomaticScanner, MS_15M

    class PendingMarketData:
        def fetch_ohlcv(self, symbol, timeframe, limit):
            assert timeframe == "15m"
            assert limit >= 40
            return [
                [10 * MS_15M, 103, 105, 103, 104, 10],
                [11 * MS_15M, 101, 102, 100, 101, 12],
            ]

    account = DemoAccount(fee_rate=0)
    book = PendingLimitBook()
    candidate = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=10 * MS_15M,
        expires_after_bars=8, invalidation=99, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[PendingLimit(1, 104), PendingLimit(2, 101)],
    )
    book.add(candidate)
    monkeypatch.setattr(runtime, "account", account)
    monkeypatch.setattr(runtime, "pending_limits", book)
    monkeypatch.setattr(runtime, "storage", None)
    monkeypatch.setattr(runtime, "manual_symbols", [])
    monkeypatch.setattr(runtime, "selected_symbols", [])
    monkeypatch.setattr(runtime, "closed_trade_events", [])
    monkeypatch.setattr("app.scanner.time.time", lambda: 14 * MS_15M / 1000)

    pending_events = []
    trade_events = []
    scanner = AutomaticScanner(
        PendingMarketData(), on_pending_event=pending_events.append,
        on_trade_event=trade_events.append,
    )
    scanner.scan_once()

    assert [(event["type"], event["leg"]) for event in pending_events] == [
        ("LIMIT_FILLED", 1), ("LIMIT_FILLED", 2)
    ]
    position = account.positions["BTC/USDT"]
    assert len(position.legs) == 2
    assert position.margin_allocated == pytest.approx(2 * account.margin_per_trade)
    assert account.margin_used == pytest.approx(2 * account.margin_per_trade)
    # Telegram fill payloads must reflect the actual single-position ledger:
    # first fill is a 5 USDT margin leg; the second updates weighted entry,
    # shared targets and total allocated margin to 10 USDT.
    assert pending_events[0]["price"] == pytest.approx(104)
    assert pending_events[0]["average_entry"] == pytest.approx(104)
    assert pending_events[0]["margin_allocated"] == pytest.approx(5)
    assert pending_events[1]["price"] == pytest.approx(101)
    assert pending_events[1]["average_entry"] == pytest.approx(position.entry)
    assert pending_events[1]["margin_allocated"] == pytest.approx(10)
    assert pending_events[1]["tp1"] == pytest.approx(position.tp1)
    assert pending_events[1]["tp2"] == pytest.approx(position.tp2)
    assert pending_events[1]["tp3"] == pytest.approx(position.tp3)
    assert pending_events[1]["time"].endswith("+00:00")
    assert candidate.last_processed_bar == 12 * MS_15M

    # A second scan of the same OHLCV rows must not duplicate fills or PnL.
    balance_after_first_scan = account.balance
    scanner.scan_once()
    assert len([event for event in pending_events if event["type"] == "LIMIT_FILLED"]) == 2
    assert account.balance == pytest.approx(balance_after_first_scan)



def test_scanner_reports_each_pending_leg_cancelled_on_structural_invalidation(monkeypatch):
    from app.demo import DemoAccount
    from app.pending_limits import PendingLimit, PendingLimitBook, PendingSetup
    from app.runtime import runtime
    from app.scanner import AutomaticScanner, MS_15M

    class InvalidatingMarketData:
        def fetch_ohlcv(self, symbol, timeframe, limit):
            return [[
                10 * MS_15M, 103, 105, 97, 100, 10,
            ]]

    book = PendingLimitBook()
    candidate = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=10 * MS_15M,
        expires_after_bars=8, invalidation=99, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[PendingLimit(1, 104), PendingLimit(2, 101)],
    )
    book.add(candidate)
    account = DemoAccount(fee_rate=0)
    monkeypatch.setattr(runtime, "account", account)
    monkeypatch.setattr(runtime, "pending_limits", book)
    monkeypatch.setattr(runtime, "storage", None)
    monkeypatch.setattr(runtime, "manual_symbols", [])
    monkeypatch.setattr(runtime, "selected_symbols", [])
    monkeypatch.setattr(runtime, "closed_trade_events", [])
    monkeypatch.setattr("app.scanner.time.time", lambda: 12 * MS_15M / 1000)

    events = []
    AutomaticScanner(
        InvalidatingMarketData(), on_pending_event=events.append,
    ).scan_once()

    assert [(event["type"], event["leg"]) for event in events] == [
        ("LIMIT_CANCELLED", 1), ("LIMIT_CANCELLED", 2)
    ]
    assert all("بی‌اعتبار" in event["reason"] for event in events)
    assert account.open_positions == []


def test_scanner_cancels_unfilled_second_limit_after_shared_tp1(monkeypatch):
    from app.demo import DemoAccount
    from app.pending_limits import PendingLimit, PendingLimitBook, PendingSetup
    from app.runtime import runtime
    from app.scanner import AutomaticScanner, MS_15M

    class ProfitThenDeepPullbackMarketData:
        rows = [
            [10 * MS_15M, 103, 105, 103, 104, 10],
            [11 * MS_15M, 106, 111, 105, 110, 12],
        ]

        def fetch_ohlcv(self, symbol, timeframe, limit):
            assert timeframe == "15m"
            return self.rows

    account = DemoAccount(fee_rate=0)
    book = PendingLimitBook()
    setup = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=10 * MS_15M,
        expires_after_bars=8, invalidation=98, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[PendingLimit(1, 104), PendingLimit(2, 101)],
    )
    book.add(setup)
    monkeypatch.setattr(runtime, "account", account)
    monkeypatch.setattr(runtime, "pending_limits", book)
    monkeypatch.setattr(runtime, "storage", None)
    monkeypatch.setattr(runtime, "manual_symbols", [])
    monkeypatch.setattr(runtime, "selected_symbols", [])
    monkeypatch.setattr(runtime, "closed_trade_events", [])
    clock = [12 * MS_15M]
    monkeypatch.setattr("app.scanner.time.time", lambda: clock[0] / 1000)

    pending_events = []
    trade_events = []
    scanner = AutomaticScanner(
        ProfitThenDeepPullbackMarketData(),
        on_pending_event=pending_events.append,
        on_trade_event=trade_events.append,
    )
    scanner.scan_once()

    assert [(event["type"], event["leg"]) for event in pending_events] == [
        ("LIMIT_FILLED", 1), ("LIMIT_CANCELLED", 2)
    ]
    assert "هدف سود" in pending_events[1]["reason"]
    assert [event["type"] for event in trade_events] == ["TP1"]
    assert trade_events[0]["time"].endswith("+00:00")
    assert trade_events[0]["price"] == pytest.approx(account.positions["BTC/USDT"].tp1)
    assert "realized_pnl" in trade_events[0]
    assert account.positions["BTC/USDT"].tp1_done is True
    assert account.positions["BTC/USDT"].closed is False
    assert setup.limits[1].status.value == "CANCELLED"

    # A later deep pullback cannot resurrect the cancelled second leg.
    scanner.market_data.rows.append([12 * MS_15M, 103, 107, 100, 103, 14])
    clock[0] = 13 * MS_15M
    scanner.scan_once()
    assert [event["type"] for event in pending_events].count("LIMIT_FILLED") == 1
    assert setup.limits[1].status.value == "CANCELLED"



def test_scanner_cancels_remaining_limit_when_shared_stop_closes_position(monkeypatch):
    from app.demo import DemoAccount
    from app.pending_limits import PendingLimit, PendingLimitBook, PendingSetup
    from app.runtime import runtime
    from app.scanner import AutomaticScanner, MS_15M

    class StopAfterFirstFillMarketData:
        def fetch_ohlcv(self, symbol, timeframe, limit):
            return [
                [10 * MS_15M, 103, 105, 103, 104, 10],
                [11 * MS_15M, 100, 105, 97, 99, 12],
            ]

    account = DemoAccount(fee_rate=0)
    book = PendingLimitBook()
    setup = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=10 * MS_15M,
        expires_after_bars=8, invalidation=98, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[PendingLimit(1, 104), PendingLimit(2, 101)],
    )
    book.add(setup)
    monkeypatch.setattr(runtime, "account", account)
    monkeypatch.setattr(runtime, "pending_limits", book)
    monkeypatch.setattr(runtime, "storage", None)
    monkeypatch.setattr(runtime, "manual_symbols", [])
    monkeypatch.setattr(runtime, "selected_symbols", [])
    monkeypatch.setattr(runtime, "closed_trade_events", [])
    monkeypatch.setattr("app.scanner.time.time", lambda: 13 * MS_15M / 1000)

    pending_events, trade_events = [], []
    AutomaticScanner(
        StopAfterFirstFillMarketData(),
        on_pending_event=pending_events.append,
        on_trade_event=trade_events.append,
    ).scan_once()

    assert [(event["type"], event["leg"]) for event in pending_events] == [
        ("LIMIT_FILLED", 1), ("LIMIT_CANCELLED", 2)
    ]
    assert "بی‌اعتبار" in pending_events[1]["reason"]
    assert [event["type"] for event in trade_events] == ["STOP"]
    assert account.positions["BTC/USDT"].closed is True
    assert account.margin_used == pytest.approx(0.0)


def test_scanner_expires_only_unfilled_leg_and_keeps_filled_position(monkeypatch):
    from app.demo import DemoAccount
    from app.pending_limits import PendingLimit, PendingLimitBook, PendingSetup
    from app.runtime import runtime
    from app.scanner import AutomaticScanner, MS_15M

    class ExpiringMarketData:
        def fetch_ohlcv(self, symbol, timeframe, limit):
            return [
                [10 * MS_15M, 103, 105, 103, 104, 10],
                [11 * MS_15M, 103, 105, 103, 104, 11],
                [12 * MS_15M, 103, 105, 103, 104, 12],
            ]

    account = DemoAccount(fee_rate=0)
    book = PendingLimitBook()
    setup = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=10 * MS_15M,
        expires_after_bars=2, invalidation=98, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[PendingLimit(1, 104), PendingLimit(2, 101)],
    )
    book.add(setup)
    monkeypatch.setattr(runtime, "account", account)
    monkeypatch.setattr(runtime, "pending_limits", book)
    monkeypatch.setattr(runtime, "storage", None)
    monkeypatch.setattr(runtime, "manual_symbols", [])
    monkeypatch.setattr(runtime, "selected_symbols", [])
    monkeypatch.setattr(runtime, "closed_trade_events", [])
    monkeypatch.setattr("app.scanner.time.time", lambda: 14 * MS_15M / 1000)

    events = []
    AutomaticScanner(
        ExpiringMarketData(), on_pending_event=events.append,
    ).scan_once()

    assert [(event["type"], event["leg"]) for event in events] == [
        ("LIMIT_FILLED", 1), ("LIMIT_CANCELLED", 2)
    ]
    assert "مهلت" in events[1]["reason"]
    assert setup.limits[0].status.value == "FILLED"
    assert setup.limits[1].status.value == "CANCELLED"
    assert account.positions["BTC/USDT"].closed is False
    assert account.margin_used == pytest.approx(5.0)


def test_two_limit_legs_can_fill_on_different_scans_and_different_candles(monkeypatch):
    from app.demo import DemoAccount
    from app.pending_limits import PendingLimit, PendingLimitBook, PendingSetup, PendingStatus
    from app.runtime import runtime
    from app.scanner import AutomaticScanner, MS_15M

    class IncrementalMarketData:
        rows = [[10 * MS_15M, 103, 105, 103, 104, 10]]

        def fetch_ohlcv(self, symbol, timeframe, limit):
            assert timeframe == "15m"
            return list(self.rows)

    market = IncrementalMarketData()
    account = DemoAccount(fee_rate=0)
    book = PendingLimitBook()
    setup = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=10 * MS_15M,
        expires_after_bars=8, invalidation=99, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[PendingLimit(1, 104), PendingLimit(2, 101)],
    )
    book.add(setup)
    monkeypatch.setattr(runtime, "account", account)
    monkeypatch.setattr(runtime, "pending_limits", book)
    monkeypatch.setattr(runtime, "storage", None)
    monkeypatch.setattr(runtime, "manual_symbols", [])
    monkeypatch.setattr(runtime, "selected_symbols", [])
    monkeypatch.setattr(runtime, "closed_trade_events", [])
    monkeypatch.setattr("app.scanner.time.time", lambda: 14 * MS_15M / 1000)

    pending_events = []
    scanner = AutomaticScanner(market, on_pending_event=pending_events.append)

    # First candle reaches only limit 1. Limit 2 must remain pending.
    scanner.scan_once()
    position = account.positions["BTC/USDT"]
    assert len(position.legs) == 1
    assert position.margin_allocated == pytest.approx(5)
    assert setup.limits[0].status is PendingStatus.FILLED
    assert setup.limits[1].status is PendingStatus.PENDING
    assert [event["leg"] for event in pending_events if event["type"] == "LIMIT_FILLED"] == [1]

    # A later closed candle reaches limit 2; it adds to the same position.
    market.rows.append([11 * MS_15M, 101, 102, 100, 101, 12])
    scanner.scan_once()
    position = account.positions["BTC/USDT"]
    assert len(position.legs) == 2
    assert position.margin_allocated == pytest.approx(10)
    assert setup.limits[1].status is PendingStatus.FILLED
    fills = [event for event in pending_events if event["type"] == "LIMIT_FILLED"]
    assert [event["leg"] for event in fills] == [1, 2]
    assert fills[0]["time"] != fills[1]["time"]
    assert fills[0]["average_entry"] == pytest.approx(104)
    assert fills[0]["margin_allocated"] == pytest.approx(5)
    assert fills[1]["average_entry"] == pytest.approx(position.entry)
    assert fills[1]["margin_allocated"] == pytest.approx(10)


def test_exit_on_later_candle_without_pending_events_is_timestamped_and_notified(monkeypatch):
    from app.demo import DemoAccount, Side
    from app.pending_limits import PendingLimit, PendingLimitBook, PendingSetup, PendingStatus
    from app.runtime import runtime
    from app.scanner import AutomaticScanner

    class OneCandleMarketData:
        def fetch_ohlcv(self, symbol, timeframe, limit):
            return [[900_000, 106.0, 111.0, 105.0, 110.0, 100.0]]

    # Leg 1 filled previously; leg 2 remains pending and is not touched here.
    book = PendingLimitBook()
    setup = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=900_000,
        expires_after_bars=8, invalidation=99.0, stop=98.0,
        ob_top=106.0, ob_bottom=100.0,
        limits=[
            PendingLimit(1, 104.0, PendingStatus.FILLED, filled_at=900_000),
            PendingLimit(2, 101.0),
        ],
        last_processed_bar=900_000,
    )
    book.add(setup)

    account = DemoAccount(fee_rate=0)
    account.add_limit_fill(
        "BTC/USDT", Side.LONG, 104.0, 98.0,
        opened_at="2026-01-01T00:15:00+00:00", leg_number=1,
    )

    # The next completed candle reaches TP1=110, but not pending limit 2=101.
    monkeypatch.setattr("app.scanner.time.time", lambda: 1800.0)
    monkeypatch.setattr(runtime, "account", account)
    monkeypatch.setattr(runtime, "pending_limits", book)
    monkeypatch.setattr(runtime, "manual_symbols", [])
    monkeypatch.setattr(runtime, "selected_symbols", [])
    monkeypatch.setattr(runtime, "storage", None)
    monkeypatch.setattr(runtime, "closed_trade_events", [])

    trade_events, pending_events = [], []
    scanner = AutomaticScanner(
        OneCandleMarketData(),
        on_trade_event=trade_events.append,
        on_pending_event=pending_events.append,
    )
    summary = scanner.scan_once()

    assert summary.errors == 0
    assert [event["type"] for event in trade_events] == ["TP1"]
    assert trade_events[0]["time"] == "1970-01-01T00:30:00+00:00"
    assert [event["type"] for event in pending_events] == ["LIMIT_CANCELLED"]
    assert pending_events[0]["leg"] == 2
    assert setup.limits[1].status is PendingStatus.CANCELLED


def test_tp1_and_deeper_limit_cancellation_are_persisted_in_one_restart_snapshot(monkeypatch, tmp_path):
    from app.demo import DemoAccount, Side
    from app.pending_limits import PendingLimit, PendingLimitBook, PendingSetup, PendingStatus
    from app.runtime import runtime
    from app.scanner import AutomaticScanner, MS_15M
    from app.storage import SQLiteStore

    class Tp1MarketData:
        def fetch_ohlcv(self, symbol, timeframe, limit):
            return [[11 * MS_15M, 106, 111, 105, 110, 12]]

    store = SQLiteStore(str(tmp_path / "tp1-cancel-atomic.sqlite3"))
    account = DemoAccount(fee_rate=0)
    account.add_limit_fill(
        "BTC/USDT", Side.LONG, 104.0, 98.0,
        opened_at="2026-01-01T00:15:00+00:00", leg_number=1,
    )
    book = PendingLimitBook()
    candidate = PendingSetup(
        symbol="BTC/USDT", side="BUY", created_at=10 * MS_15M,
        expires_after_bars=8, invalidation=99, stop=98,
        ob_top=106, ob_bottom=100,
        limits=[
            PendingLimit(1, 104, PendingStatus.FILLED, filled_at=11 * MS_15M),
            PendingLimit(2, 101),
        ],
        last_processed_bar=11 * MS_15M,
    )
    book.add(candidate)

    monkeypatch.setattr(runtime, "account", account)
    monkeypatch.setattr(runtime, "pending_limits", book)
    monkeypatch.setattr(runtime, "storage", store)
    monkeypatch.setattr(runtime, "manual_symbols", [])
    monkeypatch.setattr(runtime, "selected_symbols", [])
    monkeypatch.setattr(runtime, "closed_trade_events", [])
    monkeypatch.setattr("app.scanner.time.time", lambda: 13 * MS_15M / 1000)

    trade_events, pending_events = [], []
    AutomaticScanner(
        Tp1MarketData(), on_trade_event=trade_events.append,
        on_pending_event=pending_events.append,
    ).scan_once()

    restored_account, restored_events = store.load_state(DemoAccount(fee_rate=0))
    restored_book = store.load_pending_setups()
    restored_position = restored_account.positions["BTC/USDT"]
    restored_setup = restored_book.setups["BTC/USDT"]

    assert restored_position.tp1_done is True
    assert restored_position.closed is False
    assert restored_setup.limits[0].status is PendingStatus.FILLED
    assert restored_setup.limits[1].status is PendingStatus.CANCELLED
    assert restored_setup.limits[1].cancel_reason is not None
    assert restored_setup.last_processed_bar == 12 * MS_15M
    assert [event["type"] for event in restored_events] == ["TP1"]
    assert [event["type"] for event in trade_events] == ["TP1"]
    assert [event["type"] for event in pending_events] == ["LIMIT_CANCELLED"]
