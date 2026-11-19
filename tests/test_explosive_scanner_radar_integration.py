from __future__ import annotations

import time

from app.explosive_scanner import ExplosiveMoveScanner
from app.market_radar import RadarAlert


class FakePublicMarketData:
    def __init__(self):
        self.requests = []

    def fetch_ohlcv(self, symbol, timeframe, limit=300):
        self.requests.append((symbol, timeframe))
        durations = {
            "1d": 86_400_000,
            "4h": 14_400_000,
            "1h": 3_600_000,
            "15m": 900_000,
            "5m": 300_000,
        }
        duration = durations[timeframe]
        now = int(time.time() * 1000)
        last_open = (now // duration - 1) * duration
        rows = []
        for index in range(40):
            timestamp = last_open - (39 - index) * duration
            price = 100.0 + index * 0.01
            rows.append([timestamp, price, price + 1, price - 1, price + 0.01, 100.0])
        return rows[-limit:]

    def fetch_trades(self, symbol):
        return []

    def fetch_orderbook(self, symbol):
        return {"bids": [], "asks": []}


def test_radar_fetches_daily_data_and_emits_alert_only_for_requested_symbols(monkeypatch):
    market_data = FakePublicMarketData()
    delivered = []
    expected = RadarAlert(
        symbol="MANUAL/USDT:USDT",
        category="EARLY",
        side="BUY",
        stage="WATCH",
        score=55,
        timestamp=1_800_000_000_000,
        reference_price=100.0,
        reasons=("test reason",),
    )

    def fake_evaluate(symbol, daily, four_hour, hourly, fifteen_minute, **kwargs):
        assert len(daily) >= 30
        assert len(four_hour) >= 30
        assert len(hourly) >= 30
        assert len(fifteen_minute) >= 30
        return [expected]

    monkeypatch.setattr("app.explosive_scanner.evaluate_market_radar", fake_evaluate)
    scanner = ExplosiveMoveScanner(market_data, on_radar_alert=delivered.append)
    scanner.scan_once(["MANUAL/USDT:USDT"])

    assert ("MANUAL/USDT:USDT", "1d") in market_data.requests
    assert {symbol for symbol, _ in market_data.requests} == {"MANUAL/USDT:USDT"}
    assert delivered == [expected]
    assert scanner._radar_state[("MANUAL/USDT:USDT", "EARLY")][0] == "WATCH"
