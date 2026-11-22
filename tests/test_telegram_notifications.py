from app.telegram_notifications import format_notification


def test_limit_filled_message_shows_leg_fill_average_margin_targets_and_demo_mode():
    message = format_notification({
        "kind": "trade",
        "event": {
            "type": "LIMIT_FILLED", "symbol": "BTC/USDT", "side": "LONG",
            "leg": 2, "price": 101, "average_entry": 102.4,
            "margin_allocated": 10, "tp1": 105, "tp2": 108, "tp3": 111,
            "time": "2026-11-21T19:00:00+00:00",
        },
    })
    for expected in (
        "لیمیت شماره 2 پر شد", "BTC/USDT", "LONG",
        "قیمت پرشدن این لیمیت: 101", "میانگین ورود پوزیشن واحد: 102.4",
        "مارجین استفاده‌شده: 10 دلار", "TP1 105", "TP2 108", "TP3 111",
        "2026-11-21T19:00:00+00:00", "معاملات واقعی خاموش",
    ):
        assert expected in message


def test_limit_cancelled_message_includes_reason_and_order_price():
    message = format_notification({
        "kind": "pending",
        "event": {
            "type": "LIMIT_CANCELLED", "symbol": "ETH/USDT", "side": "SHORT",
            "leg": 1, "price": 2500, "time": 1_800_000,
            "reason": "ساختار روند نامعتبر شد",
        },
    })
    assert "لیمیت شماره 1 لغو شد" in message
    assert "ETH/USDT" in message
    assert "SHORT" in message
    assert "قیمت سفارش: 2500" in message
    assert "علت لغو: ساختار روند نامعتبر شد" in message
    assert "معاملات واقعی خاموش" in message


def test_stop_and_profit_target_messages_identify_exit_and_realized_pnl():
    stop = format_notification({
        "kind": "trade",
        "event": {
            "type": "STOP", "symbol": "BTC/USDT", "side": "LONG",
            "price": 98, "time": "2026-11-21T19:15:00+00:00",
            "realized_pnl": -1.25,
        },
    })
    assert "حد ضرر (SL) فعال شد" in stop
    assert "98" in stop
    assert "-1.2500 USDT" in stop

    tp3 = format_notification({
        "kind": "trade",
        "event": {
            "type": "TP3", "symbol": "BTC/USDT", "side": "LONG",
            "price": 111, "time": "2026-11-21T20:00:00+00:00",
            "realized_pnl": 4.75,
        },
    })
    assert "هدف سوم (TP3) زده شد" in tp3
    assert "معامله کامل بسته شد" in tp3
    assert "+4.7500 USDT" in tp3
