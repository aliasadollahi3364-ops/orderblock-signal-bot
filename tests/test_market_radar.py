from app.market_radar import (
    _bearish_structure, _bullish_structure, _entry_plan, _five_minute_trigger, _stage,
    evaluate_market_radar,
)
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
    five = series(40, 100, 0.01, 100)
    alerts = evaluate_market_radar("BTC/USDT", daily, four_hour, hourly, fifteen, five)

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


def test_high_score_is_not_confirmed_without_top_down_m5_trigger():
    daily = series(40, 100, 0.1, 100)
    four_hour = series(40, 100, 0.2, 100)
    hourly = series(40, 100, 0.05, 100)
    fifteen = series(40, 100, 0.03, 100)
    five = series(40, 100, 0.01, 100)
    alerts = evaluate_market_radar(
        "BTC/USDT", daily, four_hour, hourly, fifteen, five,
        bullish_ob_near=True, bearish_ob_near=True,
    )
    assert all(alert.stage != "CONFIRMED" for alert in alerts)


def test_ath_warning_uses_available_daily_high_but_waits_for_entry_trigger():
    daily = series(40, 100, 0.1, 100)
    daily[-1] = Candle(39 * 60_000, 104, 110, 100, 105, 250)
    four_hour = series(40, 120, -0.2, 100)
    hourly = series(40, 100, -0.05, 100)
    fifteen = series(40, 120, -0.03, 100)
    fifteen[-1] = Candle(39 * 60_000, 110, 111, 108, 109, 250)
    five = series(40, 120, -0.01, 100)

    alerts = evaluate_market_radar("BTC/USDT", daily, four_hour, hourly, fifteen, five)
    ath = next(alert for alert in alerts if alert.category == "ATH_WARNING")
    assert ath.stage == "PRE_SIGNAL"
    assert ath.score >= 80
    assert "تاریخچه روزانه موجود" in " ".join(ath.reasons)


def test_bottom_reversal_uses_available_daily_low_and_waits_for_entry_trigger():
    daily = series(40, 100, 0.1, 100)
    daily[-1] = Candle(39 * 60_000, 94, 98, 90, 94.4, 250)
    four_hour = series(40, 100, 0.2, 100)
    hourly = series(40, 100, 0.05, 100)
    fifteen = series(40, 100, 0.03, 100)
    previous_high = max(bar.high for bar in fifteen[-7:-1])
    fifteen[-1] = Candle(39 * 60_000, previous_high, previous_high + 3,
                         99, previous_high + 2, 250)
    five = series(40, 100, 0.01, 100)

    alerts = evaluate_market_radar("BTC/USDT", daily, four_hour, hourly, fifteen, five)
    bottom = next(alert for alert in alerts if alert.category == "BOTTOM_REVERSAL")
    assert bottom.stage == "PRE_SIGNAL"
    assert bottom.score >= 80
    assert "تاریخچه روزانه موجود" in " ".join(bottom.reasons)


def test_order_block_confluence_adds_an_explicit_reason():
    daily = series(40, 100, 0.1, 100)
    four_hour = series(40, 100, 0.2, 100)
    hourly = series(40, 100, 0.05, 100)
    fifteen = series(40, 100, 0.03, 100)
    five = series(40, 100, 0.01, 100)
    alerts = evaluate_market_radar(
        "BTC/USDT", daily, four_hour, hourly, fifteen, five,
        bullish_ob_near=True,
    )
    early = next(alert for alert in alerts if alert.category == "EARLY")
    assert any("اردربلاک صعودی فعال ۴ساعته" in reason for reason in early.reasons)


def test_single_candle_breakout_is_not_counted_as_full_structure_shift():
    flat = [Candle(i * 60_000, 100, 101, 99, 100, 100) for i in range(8)]
    flat[-1] = Candle(7 * 60_000, 100, 103, 99, 102, 200)
    assert not _bullish_structure(flat)
    assert not _bearish_structure(flat)


def test_five_minute_trigger_requires_closed_candle_breakout():
    bars = series(5, 100, 0.01, 100)
    assert not _five_minute_trigger(bars, "BUY")
    bars[-1] = Candle(bars[-1].timestamp, 101.04, 102.20, 101.03, 102.18, 100)
    assert _five_minute_trigger(bars, "BUY")



def test_m5_entry_plan_has_structural_stop_and_2r_3r_4r_targets():
    bars = []
    for index in range(20):
        price = 100 + index * 0.05
        bars.append(Candle(index * 300_000, price, price + 0.08,
                           price - 0.08, price + 0.04, 100))
    bars[-1] = Candle(bars[-1].timestamp, 101.02, 101.20, 101.00, 101.18, 150)
    plan = _entry_plan("BUY", bars)
    assert plan is not None
    entry, stop, tp1, tp2, tp3 = plan
    risk = entry - stop
    assert stop < entry < tp1 < tp2 < tp3
    assert abs((tp1 - entry) / risk - 2) < 1e-9
    assert abs((tp2 - entry) / risk - 3) < 1e-9
    assert abs((tp3 - entry) / risk - 4) < 1e-9


def test_m5_entry_plan_rejects_wide_stop():
    bars = series(20, 100, 0.01, 100)
    assert _entry_plan("BUY", bars) is None
