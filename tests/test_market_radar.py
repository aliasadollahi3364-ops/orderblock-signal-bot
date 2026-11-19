from app.market_radar import evaluate_market_radar
from app.strategy import Candle


def series(count=40, start=100.0, step=0.1, volume=100.0):
    bars = []
    for i in range(count):
        price = start + i * step
        bars.append(Candle(i * 60_000, price, price + 1, price - 1,
                           price + step, volume))
    return bars


def test_radar_requires_sufficient_history():
    assert evaluate_market_radar("BTC/USDT", [], [], [], []) == []


def test_radar_alerts_are_independent_and_alert_only():
    daily = series(40, 100, 0.1, 100)
    four_hour = series(40, 100, 0.2, 100)
    hourly = series(40, 100, 0.05, 100)
    fifteen = series(40, 100, 0.03, 100)
    alerts = evaluate_market_radar("BTC/USDT", daily, four_hour, hourly, fifteen)

    assert all(alert.symbol == "BTC/USDT" for alert in alerts)
    assert all(alert.category in {
        "EARLY", "BULLISH_BREAKOUT", "ATH_WARNING",
        "BEARISH_BREAKDOWN", "BOTTOM_REVERSAL",
    } for alert in alerts)
    assert all(alert.stage in {"WATCH", "PRE_SIGNAL", "CONFIRMED"} for alert in alerts)
    assert all(0 <= alert.score <= 100 for alert in alerts)
    assert all(alert.side in {"BUY", "SELL"} for alert in alerts)
