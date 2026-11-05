# Order Block Signal Bot

A Telegram-connected, web-managed **demo trading** bot for WEEX market data.

## Safety scope

- Demo trading only; no live orders are sent to WEEX.
- Initial demo balance: **$100**.
- Margin per trade: **$5**.
- Leverage: **5x** (nominal position value: **$25**).

## Strategy source of truth

The implementation follows only the user's supplied Pine strategy. It is based on 4-hour Order Blocks and 15-minute entry confirmation. Do not merge older bot strategies or add filters absent from the source script.

## Demo trade management

- TP1 at 1R: close 33% of the position.
- TP2 at 2R: close 33% of the position.
- TP3 at 3R: close the remainder.
- After TP1, move stop loss to entry.
- After TP2, move stop loss to TP1.
- Record realized P&L and publish trade events to Telegram and the read-only API.

## Persistence

The internal demo ledger is stored in SQLite at `DATABASE_PATH` (default `./data/demo.sqlite3`). The account balance, every position (including partial-target flags, remaining quantity, moved stop and realized P&L), and the event history are committed in a single SQLite transaction. State is restored before the scanner starts.

On a hosting provider, the database file must live on a persistent disk/volume. A normal ephemeral filesystem can still lose the database when the service is replaced or redeployed. Back up the database file periodically.

## Scanner and market data

- Public-only CCXT market data for active linear USDT perpetual markets.
- Two-stage scanner: rank up to 500 markets, then evaluate up to 60 markets with active 4H OBs using the 15m strategy engine.
- Liquidity ranking currently uses 24h quote volume and bid/ask spread; exchange-wide order-book depth ranking is not implemented.
- Live WEEX responses and candle-by-candle TradingView parity still require verification.

## Run locally

Install dependencies with `pip install -r requirements.txt`, copy `.env.example` to `.env`, and start with `python main.py`. Set Telegram credentials to enable Telegram polling. Never commit secrets.

## Status

The project includes the strategy engine, demo ledger, two-stage scanner, read-only API, basic Telegram commands, and SQLite persistence. Automated tests and live WEEX verification must be run before relying on it. No live trading functionality is implemented.
