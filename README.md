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

## Iranian 18k gold (Talasanj)

An independent, signal-only Iranian 18k gold data path is available behind the optional `TALASANJ_API_KEY` setting.

- Get a free API key from Telegram bot `@taalasanj_bot` → API برنامه‌نویسان → ساخت کلید.
- Store it only as the HOKAS/host environment variable `TALASANJ_API_KEY`; never paste it into Telegram chats, GitHub, or source code.
- The service reads `gram18`, `usd`, and `ounce` from Talasanj `GET /api/v1/prices`, samples at the provider's 20-second cadence, and persists samples/candles in the same SQLite file under separate `gold_*` tables.
- Local 5m, 15m, 1h and 4h candles are built from the sampled Iranian 18k price. Intraday minute history is seeded once at startup when available.
- The existing strategy configuration is reused without changing the WEEX scanner or its manual symbol list. The gold signal engine evaluates confirmed 4h structure and 15m entries; it does not place orders or alter the USDT demo ledger.
- Gold status is available through Telegram `/gold`. The signal caption includes entry, stop, 1R/2R/3R targets, and a link attribution to Talasanj.
- The free API requires visible attribution: `قیمت‌ها از طلاسنج · https://talasanj.info`. Do not remove it.
- This provider is indicative and not guaranteed execution-grade market data. Sampled prices do not contain real traded volume; AVP/volume-profile filters are therefore not fabricated or enabled.
- The supplied strategy has a 4h SMA200 filter. The engine waits until enough locally collected 4h candles exist; it must not emit a signal during warm-up. Historical API coverage is intraday minute points for the current day and daily closes for longer ranges, not historical 4h OHLC. Consequently, a long warm-up may be required after first installation.

If the API key is absent, the gold scanner remains disabled and all existing crypto functionality continues unchanged.

## Run locally

Install dependencies with `pip install -r requirements.txt`, copy `.env.example` to `.env`, and start with `python main.py`. Set Telegram credentials to enable Telegram polling. Never commit secrets.

## Status

The project includes the strategy engine, demo ledger, two-stage scanner, read-only API, basic Telegram commands, and SQLite persistence. Automated tests and live WEEX verification must be run before relying on it. No live trading functionality is implemented.
