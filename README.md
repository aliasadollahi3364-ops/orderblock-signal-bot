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
- Internal demo ledger with $100 starting balance, $5 margin, 5x leverage, 1R/2R/3R partial targets, and stop movement after TP1/TP2.
- Initial unit tests for OB creation/invalidation and demo position accounting.
- Validated environment settings with a hard guard against enabling live trading.

The Telegram bot, persistent database, full market scanner orchestration, web dashboard, and HOKAS deployment are still pending. The current market-data adapter has not yet been verified against live WEEX responses, and the strategy tests have not yet been run in a deployment environment.

## Configuration

Copy `.env.example` to `.env` and fill in the required credentials. Never commit secrets.

## Status

The first strategy and demo-account implementation has been committed. It still requires test execution, comparison against TradingView candle-by-candle, WEEX market-data verification, Telegram/web integration, and deployment. No live trading functionality is implemented.
