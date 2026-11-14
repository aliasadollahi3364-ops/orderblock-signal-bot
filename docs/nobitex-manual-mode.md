# Nobitex manual-symbol mode

The feature branch defaults to Nobitex and keeps the existing demo-only strategy engine.
Telegram `/add BTC` resolves the public `BTCUSDT` market using the public
`/market/stats` endpoint. OHLC candles are read from `/market/udf/history`.
No account or order endpoints are called.

## Important limitation without a token

Nobitex documents `GET /margin/markets/list` with token authentication.
Without a token, public market statistics can confirm that a public USDT
market exists, but **cannot prove that the same market is enabled for margin
trading or whether long and short are each enabled**. Therefore, manually add
only symbols that you have independently confirmed in Nobitex's margin UI.
The bot currently restricts this demo account to USDT-quoted symbols because
its balance, PnL and fees are denominated in USDT.

If a read-only token is supplied later, the adapter can read the margin catalog
and direction flags. It still has no order-placement implementation.

## Preserved Pine strategy logic

The existing `OrderBlockStrategy` is not replaced:
- 4H order-block creation uses the Pine quality filters and confirmed 4H candles.
- The 15m OB route retains OB touch/freshness, CHoCH strength, retest and candle confirmation.
- The separate 15m trendline entry route retains break → later pullback/retest → directional candle confirmation. **It is a separate entry route, but not OB-free:** the original Pine source also requires the signal candle to touch an active 4H OB whose direction matches the signal, plus the independent SMA200 trendline filter when enabled.
- Default risk settings match the supplied Pine source: risk 1%, R:R 2.5, SL buffer 2.0 ATR, OB-boundary SL enabled, and trailing disabled by default.
- The demo ledger keeps its existing fixed 5 USDT margin × 5x sizing and TP1/TP2/TP3 event history; this is the bot's paper-account model, not TradingView's risk-percent position sizing.

The Nobitex adapter and scanner wiring are on the
`nobitex-margin-expansion` branch only. This is not a deployment instruction.

## Independent explosive-move alerts

The separate alert engine checks closed 4H, 1H, 15m and 5m candles, public
trade aggressor volumes and the top levels of the public order book. It scores
compression, relative volume, approximate trade-flow, a same-direction 4H
order block, liquidity sweep, CHoCH, breakout/retest and 1H trend strength.
The proposed thresholds are an early warning at 75/100 and a confirmed alert
at 85/100 with volume, trade-flow, order-book, OB, CHoCH, retest and 5m timing
gates. Repeated early warnings are suppressed for four hours; a confirmed
alert upgrades an earlier warning.

These scores are heuristic filters, not probabilities or guarantees of a pump.
The engine is alert-only: it does not open demo positions and cannot place live
orders. Since tokenless mode cannot verify margin direction permissions, check
that the selected market and the alerted LONG/SHORT direction are enabled in
your Nobitex margin account before acting on any alert.
