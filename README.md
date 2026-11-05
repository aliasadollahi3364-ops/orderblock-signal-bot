# Order Block Signal Bot

A Telegram-connected, web-managed **demo trading** bot for WEEX market data.

## Safety scope

- Demo trading only.
- No live orders are sent to WEEX.
- Initial demo balance: **$100**.
- Margin per trade: **$5**.
- Leverage: **5x** (nominal position value: **$25**).

## Strategy source of truth

The implementation must follow only the user's latest Pine Script strategy:

`WEEX | Order Blocks + CHoCH + SMA200 Filter + Trailing Stop`

The strategy is based on 4-hour Order Blocks and 15-minute entry confirmation. Do not merge older bot strategies or add filters that are not present in the source script.

The full source Pine Script must be available before the strategy engine is implemented and validated. The lightweight scanner table at the end of the Pine script is not a substitute for the main strategy logic.

## Demo trade management

The agreed demo position management is:

- TP1 at 1R: close 33% of the position.
- TP2 at 2R: close 33% of the position.
- TP3 at 3R: close the remaining 34%.
- After TP1, move stop loss to entry.
- After TP2, move stop loss to TP1.
- Record realized and unrealized P&L and publish trade events to Telegram and the web panel.

## Implemented foundation

- Closed-candle Python engine for confirmed 4H BOS / Order Block creation and invalidation.
- 15m pivot, ATR, OB touch/freshness, CHoCH, retest, confirmation-candle, optional SMA200 and optional 4H trend-filter logic.
- Public-only CCXT market-data adapter for active linear USDT perpetual markets.
- Internal demo ledger supporting multiple independent positions, $100 starting balance, $5 margin per trade, 5x leverage, 1R/2R/3R partial targets, and stop movement after TP1/TP2.
- Read-only FastAPI endpoints for status, account, positions, and event history.
- Initial Telegram commands: /start, /status, /demo, /open, /stats, /help.
- GitHub Actions workflow to run the Python unit tests.
- Validated environment settings with a hard guard against enabling live trading.

The persistent database, automatic 500-market/60-candidate scanner orchestration, signal-to-demo auto-entry loop, rich web dashboard, Telegram signal notifications, and HOKAS deployment are still pending. The current market-data adapter has not yet been verified against live WEEX responses, and CI results must be checked before relying on the implementation.

## Run locally

Install dependencies with `pip install -r requirements.txt`, copy `.env.example` to `.env`, and start with `python main.py`. Set `TELEGRAM_BOT_TOKEN` to enable Telegram polling; the HTTP API can start without it. Never commit secrets.

The current dashboard API is read-only and reports scanner state as NOT_STARTED until the scanner orchestration is implemented.

## Status

The strategy engine, demo accounting, read-only API, and basic Telegram commands have been committed. They still require test execution, candle-by-candle comparison against TradingView, WEEX market-data verification, scanner orchestration, and deployment. No live trading functionality is implemented.
