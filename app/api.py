"""Read-only FastAPI dashboard endpoints for the internal demo account."""
from __future__ import annotations

from datetime import timezone

from fastapi import FastAPI

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


@app.get("/")
def dashboard():
    return {
        "dashboard": "Order Block Signal Bot",
        "mode": "DEMO_ONLY",
        "api": ["/health", "/api/status", "/api/account", "/api/positions", "/api/events"],
        "note": "Web UI is a minimal API landing page; interactive dashboard is pending.",
    }
