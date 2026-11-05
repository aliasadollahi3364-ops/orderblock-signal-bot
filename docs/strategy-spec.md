# Strategy implementation specification

This document records the exact source strategy supplied by the project owner on 2026-11-05. The full Pine Script source was supplied in the conversation under the title:

`WEEX | Order Blocks + CHoCH + SMA200 Filter + Trailing Stop` (Pine Script v6).

## Source-of-truth rules

1. Implement the main chart strategy (the 4H Order Block engine and 15m entry logic), not the small 10-symbol table scanner at the bottom of the Pine file.
2. Do not merge older strategies or introduce unrequested filters.
3. Use only closed 4H candles for confirmed HTF events.
4. Signal evaluation is on closed 15m candles.
5. FVG is visual only and has no role in signal generation.
6. The Pine source's risk sizing and single 2R exit are not used for the internal demo account sizing/partial exits described below. The signal's initial stop is derived from the Pine logic.

## Defaults supplied in Pine

### 4H Order Blocks
- HTF: 240 minutes
- Pivot left/right: 3
- Body moving average length: 10
- Opposite-candle search: 12 bars
- Stored OB maximum: 200
- A BOS occurs when the 4H close crosses the last confirmed pivot high/low.
- A qualifying BOS candle body must exceed the prior value of the 10-candle average body.
- Bullish OB: nearest prior bearish candle; zone is its high/low.
- Bearish OB: nearest prior bullish candle; zone is its high/low.
- Confirmed HTF values are requested with the prior HTF bar to avoid lookahead.
- Bullish OB invalidates only on a confirmed 4H close below its bottom; bearish OB invalidates only on a confirmed 4H close above its top.

### 15m entry
- Pivot length: 3
- CHoCH wait limit: 17 bars
- ATR length: 7
- 4H BOS trend filter: OFF
- OB touch required: ON
- Touch freshness: ON, maximum 14 bars since touch
- CHoCH required: ON
- CHoCH strength filter: ON; minimum body 0.85 ATR
- CHoCH retest required: ON; tolerance 0.15 ATR
- Directional confirmation candle required: ON
- Confirmation strength filter: ON; minimum body 0.20 ATR
- Long confirmation: bullish candle; when retest filter is enabled, close must be above retest level.
- Short confirmation: bearish candle; when retest filter is enabled, close must be below retest level.
- Only an active, unused, directionally eligible OB may generate an entry.
- The first qualifying OB in stored-array order is selected, matching the Pine loop's break behavior.

### Initial stop
- OB-boundary stop: ON
- Buffer: 0.15 ATR
- Long stop: min(last confirmed 15m pivot low, OB bottom) minus ATR buffer.
- Short stop: max(last confirmed 15m pivot high, OB top) plus ATR buffer.
- Stop must be on the correct side of the signal close and at least one minimum tick away.

### Optional filters and defaults
- SMA length: 200
- SMA display: OFF
- SMA entry filter: OFF (if enabled, longs require close above SMA200 and shorts below)
- 4H BOS trend alignment filter: OFF
- FVG display: ON by default, visual only
- Pine trailing stop: OFF by default. If enabled, the source uses a fixed distance equal to initial entry-to-stop risk distance, not an ATR-multiple distance, and only ratchets in the profitable direction.
- Break-even: ON; trigger default 1.0R.

## Demo account behavior separately agreed for this bot

- Starting balance: $100
- Fixed margin per position: $5
- Leverage: 5x
- Notional position value: $25
- No live trading and no live order endpoints.
- Three partial targets: TP1=1R close 33%; TP2=2R close 33%; TP3=3R close remaining 34%.
- After TP1, move stop to entry.
- After TP2, move stop to TP1.
- Track realized/unrealized P&L and all trade events.

## Important implementation details to validate

- Pine's `strategy.position_size`, `syminfo.pointvalue`, tick size, contract quantity, and fee/slippage semantics do not map automatically to a linear USDT perpetual. Validate WEEX contract metadata and demo P&L calculations explicitly.
- Preserve event timestamps and use deterministic candle ordering for bars where both a stop and target could be touched; document the conservative fill assumption in tests.
- Avoid lookahead: a 4H OB may only be used after its 4H candle is confirmed.
- The small Pine scanner function at the end is a simplified status display, not an exact implementation of the main strategy. Do not treat its ten hard-coded symbols as the bot's market universe.
