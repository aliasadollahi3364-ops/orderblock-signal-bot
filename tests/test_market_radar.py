from app.market_radar import _bearish_structure, _bullish_structure, _stage, evaluate_market_radar
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



def test_alert_stage_thresholds():
    assert _stage(49) is None
    assert _stage(50) == "WATCH"
    assert _stage(65) == "PRE_SIGNAL"
    assert _stage(80) == "CONFIRMED"


def test_ath_warning_uses_available_daily_high_and_daily_volume():
    daily = series(40, 100, 0.1, 100)
    daily[-1] = Candle(39 * 60_000, 104, 110, 100, 105, 250)
    four_hour = series(40, 120, -0.2, 100)
    hourly = series(40, 100, -0.05, 100)
    fifteen = series(40, 120, -0.03, 100)
    fifteen[-1] = Candle(39 * 60_000, 110, 111, 108, 109, 250)

    alerts = evaluate_market_radar("BTC/USDT", daily, four_hour, hourly, fifteen)
    ath = next(alert for alert in alerts if alert.category == "ATH_WARNING")
    assert ath.stage == "CONFIRMED"
    assert ath.score >= 80
    assert "تاریخچه روزانه موجود" in " ".join(ath.reasons)


def test_bottom_reversal_uses_available_daily_low_and_bullish_reversal():
    daily = series(40, 100, 0.1, 100)
    daily[-1] = Candle(39 * 60_000, 94, 98, 90, 94.4, 250)
    four_hour = series(40, 100, 0.2, 100)
    hourly = series(40, 100, 0.05, 100)
    fifteen = series(40, 100, 0.03, 100)
    previous_high = max(bar.high for bar in fifteen[-7:-1])
    fifteen[-1] = Candle(39 * 60_000, previous_high, previous_high + 3,
                         99, previous_high + 2, 250)

    alerts = evaluate_market_radar("BTC/USDT", daily, four_hour, hourly, fifteen)
    bottom = next(alert for alert in alerts if alert.category == "BOTTOM_REVERSAL")
    assert bottom.stage == "CONFIRMED"
    assert bottom.score >= 80
    assert "تاریخچه روزانه موجود" in " ".join(bottom.reasons)



def test_order_block_confluence_adds_an_explicit_reason():
    daily = series(40, 100, 0.1, 100)
    four_hour = series(40, 100, 0.2, 100)
    hourly = series(40, 100, 0.05, 100)
    fifteen = series(40, 100, 0.03, 100)
    alerts = evaluate_market_radar(
        "BTC/USDT", daily, four_hour, hourly, fifteen, bullish_ob_near=True
    )
    early = next(alert for alert in alerts if alert.category == "EARLY")
    assert any("اردربلاک صعودی فعال ۴ساعته" in reason for reason in early.reasons)


def test_single_candle_breakout_is_not_counted_as_full_structure_shift():
    flat = [Candle(i * 60_000, 100, 101, 99, 100, 100) for i in range(8)]
    flat[-1] = Candle(7 * 60_000, 100, 103, 99, 102, 200)
    assert not _bullish_structure(flat)
    assert not _bearish_structure(flat)
