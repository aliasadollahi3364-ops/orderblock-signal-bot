# Order Block Signal Bot

A Telegram-connected, web-managed **demo trading** bot for WEEX market data.

## Safety scope

- Demo trading only; no live orders are sent to WEEX.
- Initial demo balance: **$100**.
- Margin per trade: **$5**.
- Leverage: **5x** (nominal position value: **$25**).

## Strategy source of truth

The implementation follows the user's supplied Pine v6 strategy: confirmed 4H Order Blocks/BOS and two 15m entry methods. The ORDER_BLOCK path requires price to revisit a 4H OB, then a 15m CHoCH, retest and confirmation. The TRENDLINE path requires a valid 15m trendline break followed by a later pullback and confirmation; a breakout candle alone must never emit a signal. The trendline direction is gated by the nearest active 4H OB touched by the signal candle. Both methods use the source's 4H SMA200 filters. Do not merge older bot strategies or add filters absent from the source script.

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
- Two-stage scanner: rank up to 500 markets, then evaluate up to 60 markets with active 4H OBs using both 15m entry methods.
- Liquidity ranking currently uses 24h quote volume and bid/ask spread; exchange-wide order-book depth ranking is not implemented.
- Live WEEX responses and candle-by-candle TradingView parity still require verification.

## Run locally

Install dependencies with `pip install -r requirements.txt`, copy `.env.example` to `.env`, and start with `python main.py`. Set Telegram credentials to enable Telegram polling. Never commit secrets.

## Status

The project includes the strategy engine, demo ledger, two-stage scanner, read-only API, basic Telegram commands, and SQLite persistence. Automated tests and live WEEX verification must be run before relying on it. No live trading functionality is implemented.


## Iranian 18k gold investment analysis

An independent Telegram module samples Talasanj prices for 18k gold, the free-market USD rate, and the global ounce. It calculates the theoretical 18k gram price, compares it with the reported local price, and sends a once-daily Tehran-time report (default 10:00). Immediate buy alerts require both a BUY signal from the existing 4H order-block / 15m strategy and a market score of at least 65/100. It never selects a gold fund, opens a crypto demo position, or submits any order.

Set `TALASANJ_API_KEY` privately in the hosting environment. Optional settings: `GOLD_POLL_SECONDS` (default 20) and `GOLD_DAILY_REPORT_HOUR` (default 10, Tehran time). The free Talasanj API key can be created through `@taalasanj_bot`; attribution is included in reports.

The existing strategy's 4H SMA200 filter is not disabled. Talasanj's intraday history is limited, so the gold strategy must first collect 200 closed 4H candles locally (roughly 34 days of continuous data) before it can issue confirmed strategy alerts. Daily valuation reports can start as soon as live prices arrive.
