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
