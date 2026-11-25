"""Read-only FastAPI dashboard endpoints for the internal demo account."""
from __future__ import annotations

from datetime import timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.runtime import runtime

app = FastAPI(title="Order Block Signal Bot", version="0.1.0")


def _iso(value):
    return value.astimezone(timezone.utc).isoformat() if value else None


@app.get("/health")
def health():
    return {"ok": True, "mode": "DEMO_ONLY", "live_trading": False}


@app.get("/api/status")
def status():
    return {
        "mode": "DEMO_ONLY",
        "scanner_state": runtime.scanner_state,
        "started_at": _iso(runtime.started_at),
        "last_scan_at": _iso(runtime.last_scan_at),
        "selected_symbols": runtime.selected_symbols,
        "signal_count": runtime.signal_count,
        "live_trading_enabled": False,
    }


@app.get("/api/account")
def account():
    acct = runtime.account
    return {
        "initial_balance": acct.initial_balance,
        "balance": acct.balance,
        "margin_per_trade": acct.margin_per_trade,
        "leverage": acct.leverage,
        "notional_per_trade": acct.notional,
        "margin_used": acct.margin_used,
        "free_margin": acct.free_margin,
        "open_positions": len(acct.open_positions),
    }


@app.get("/api/positions")
def positions():
    return [
        {
            "symbol": p.symbol,
            "side": p.side.value,
            "entry": p.entry,
            "stop": p.stop,
            "initial_stop": p.initial_stop,
            "quantity_initial": p.quantity_initial,
            "quantity_remaining": p.quantity_remaining,
            "tp1": p.tp1,
            "tp2": p.tp2,
            "tp3": p.tp3,
            "tp1_done": p.tp1_done,
            "tp2_done": p.tp2_done,
            "closed": p.closed,
            "realized_pnl": p.realized_pnl,
        }
        for p in runtime.account.positions.values()
    ]


@app.get("/api/events")
def events():
    return runtime.closed_trade_events[-100:]


@app.get("/gold", response_class=HTMLResponse)
def gold_page():
    """Public, server-rendered gold price page with Talasanj's required visible credit."""
    service = runtime.gold_market
    prices = getattr(service, "latest_prices", {}) if service else {}
    updated = getattr(service, "last_updated_at", None) if service else None
    error = getattr(service, "last_error", None) if service else None

    price_text = "هنوز قیمت زنده دریافت نشده است"
    if prices.get("gram18") is not None:
        price_text = f"{float(prices['gram18']):,.0f} تومان"
    elif service:
        try:
            latest = service.store.latest_tick()
            if latest:
                price_text = f"{float(latest[1]):,.0f} تومان (آخرین داده ذخیره‌شده)"
        except Exception:
            pass

    updated_text = str(updated) if updated else "نامشخص"
    error_html = (
        f'<p class="error">وضعیت دریافت داده: {str(error)[:300]}</p>'
        if error else ""
    )
    html = f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>قیمت زنده طلای ۱۸ عیار</title>
  <style>
    body {{ margin: 0; padding: 32px 16px; background: #f5f7fa; color: #172033;
           font-family: Tahoma, Arial, sans-serif; }}
    main {{ max-width: 620px; margin: 30px auto; padding: 28px; background: white;
            border-radius: 16px; box-shadow: 0 8px 28px #17203312; }}
    h1 {{ font-size: 22px; }} .price {{ font-size: 28px; font-weight: bold; margin: 24px 0; }}
    .muted {{ color: #596579; font-size: 14px; }} .credit {{ margin-top: 28px; }}
    .credit a {{ color: #075fc7; text-decoration: underline; }}
    .error {{ color: #9b2c2c; overflow-wrap: anywhere; font-size: 13px; }}
  </style>
</head>
<body>
  <main>
    <h1>قیمت طلای ۱۸ عیار ایران</h1>
    <div class="price">{price_text}</div>
    <p class="muted">آخرین به‌روزرسانی: {updated_text}</p>
    {error_html}
    <p class="muted">این صفحه فقط برای نمایش اطلاعات بازار است و هیچ سفارش معاملاتی ثبت نمی‌کند.</p>
    <p class="credit"><a href="https://talasanj.info">قیمت‌ها از طلاسنج</a></p>
  </main>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/")
def dashboard():
    return {
        "dashboard": "Order Block Signal Bot",
        "mode": "DEMO_ONLY",
        "api": ["/health", "/api/status", "/api/account", "/api/positions", "/api/events", "/gold"],
        "gold_page": "/gold",
        "note": "Read-only API; public gold attribution page is available at /gold.",
    }
