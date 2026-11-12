# Strategy implementation specification

Source of truth: user-supplied Pine Script v6, titled WEEX 4H Order Blocks + 15m Trendline Break + SMA200 + Trailing Stop.

- Only confirmed 4H candles create/invalidate OBs and update the 4H SMA.
- 4H BOS quality: ATR(14), body >= prior 10-bar average body * 1.20 and >= ATR * 0.65; BOS close displacement >= ATR * 0.10; source opposite candle range <= its ATR * 2.50.
- 4H pivots 3/3; opposite candle search 12; max stored OB 200.
- Entries are evaluated only on confirmed 15m candles. 15m pivots 3/3; ATR 7.
- ORDER_BLOCK path uses OB touch/freshness, CHoCH and retest/confirmation filters.
- TRENDLINE path uses the latest two confirmed 15m pivot highs/lows, descending resistance or ascending support, close breakout, optional pullback/confirmation, and nearest active 4H OB touched by the signal candle. Used OBs remain eligible for this direction check, as in Pine.
- Both methods use the 4H SMA200 gates enabled in the source. ORDER_BLOCK uses useSMA200Filter; TRENDLINE uses useSMA200TrendlineFilter.
- Defaults retained: trend filter ON, SL buffer 2.0 ATR, riskReward 2.5, riskPercent 1.0, OB-boundary stop ON, trailing OFF, trailing ATR multiplier 1.5.
- Trendline stop is the latest opposite 15m pivot +/- ATR * 2.0. OB stop uses the OB boundary and opposite pivot with the same buffer.
- Pine has one 2.5R target. The separate demo ledger remains unchanged by prior agreement: fixed $100 starting balance, $5 margin, 5x leverage, partial demo targets 1R/2R/3R and BE/trailing ledger behavior. These are demo-account bookkeeping rules, not Pine signal parameters.
- The small 10-symbol Pine table is not the bot universe. The bot keeps its WEEX two-stage market scanner.
- Demo only: public market data; no private account endpoints and no real orders.
