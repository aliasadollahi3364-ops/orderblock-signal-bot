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

## Planned components

- Python strategy and market-data engine.
- Internal demo account and position ledger.
- Telegram notifications and commands.
- Web dashboard for demo balance, open positions, history, and settings.
- Automated tests for strategy conditions and trade accounting.

## Configuration

Copy `.env.example` to `.env` and fill in the required credentials. Never commit secrets.

## Status

Repository initialized. The strategy engine and execution simulator are not yet implemented; implementation is intentionally blocked until the complete source Pine Script is available for a faithful translation.
