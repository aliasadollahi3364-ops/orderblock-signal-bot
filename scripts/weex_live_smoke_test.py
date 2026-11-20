"""Read-only WEEX public market-data smoke test.

This script uses only public market endpoints through the same CCXT adapter
as the bot. It never authenticates or places orders.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
import sys

# Running a script from scripts/ puts that directory first on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.market_data import WeexMarketData
from app.explosive_scanner import DURATION_MS, _closed_candles, _ob_for_side
from app.market_radar import evaluate_market_radar


TIMEFRAME_MS = {"1d": 86_400_000, "4h": 14_400_000, "1h": 3_600_000, "15m": 900_000, "5m": 300_000}
TIMEFRAMES = tuple(TIMEFRAME_MS)


def main() -> None:
    market_data = WeexMarketData(exchange_id="weex", quote_currency="USDT")
    try:
        candidate = market_data.resolve_manual_symbol("BTC")
        print(f"Resolved public contract: {candidate.symbol}")
        print(f"Last price: {candidate.last_price}")

        failures: list[str] = []
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        closed_bars = {}
        limits = {"1d": 400, "4h": 220, "1h": 120, "15m": 320, "5m": 120}
        for timeframe in TIMEFRAMES:
            try:
                rows = market_data.fetch_ohlcv(candidate.symbol, timeframe, limit=limits[timeframe])
                if not rows:
                    raise RuntimeError("exchange returned no candles")

                valid = []
                for row in rows:
                    if len(row) < 6:
                        continue
                    values = [float(value) for value in row[:6]]
                    if all(math.isfinite(value) for value in values) and values[0] > 0:
                        valid.append(values)
                if not valid:
                    raise RuntimeError("no valid OHLCV rows in response")

                closed = [
                    row for row in valid
                    if int(row[0]) + TIMEFRAME_MS[timeframe] <= now_ms
                ]
                if not closed:
                    raise RuntimeError("no fully closed candles in response")
                closed_bars[timeframe] = _closed_candles(rows, timeframe, now_ms)
                if len(closed_bars[timeframe]) < 30:
                    raise RuntimeError(f"only {len(closed_bars[timeframe])} valid closed candles")
                latest_closed = closed[-1]
                age_since_close = max(
                    0, (now_ms - (int(latest_closed[0]) + TIMEFRAME_MS[timeframe])) // 60_000
                )
                print(
                    f"PASS {timeframe}: rows={len(valid)}, closed_rows={len(closed)}, "
                    f"last_closed_close={latest_closed[4]}, "
                    f"minutes_since_close={age_since_close}"
                )
            except Exception as exc:  # report each timeframe independently
                failures.append(f"{timeframe}: {type(exc).__name__}: {exc}")
                print(f"FAIL {timeframe}: {type(exc).__name__}: {exc}")

        if failures:
            raise SystemExit("WEEX live smoke test failed: " + " | ".join(failures))

        radar_price = closed_bars["15m"][-1].close
        bull_ob, _, _ = _ob_for_side(closed_bars["4h"], "BUY", radar_price)
        bear_ob, _, _ = _ob_for_side(closed_bars["4h"], "SELL", radar_price)
        alerts = evaluate_market_radar(
            candidate.symbol,
            closed_bars["1d"],
            closed_bars["4h"],
            closed_bars["1h"],
            closed_bars["15m"],
            closed_bars["5m"],
            bullish_ob_near=bull_ob,
            bearish_ob_near=bear_ob,
        )
        print(
            "RADAR LIVE EVALUATION: "
            f"bullish_4h_ob_near={bull_ob}, bearish_4h_ob_near={bear_ob}, "
            f"alerts={len(alerts)}"
        )
        if alerts:
            for alert in alerts:
                print(
                    f"RADAR {alert.category} {alert.side} {alert.stage} "
                    f"score={alert.score} reference={alert.reference_price} "
                    f"entry={alert.entry_price} sl={alert.stop_loss} "
                    f"tp1={alert.tp1} tp2={alert.tp2} tp3={alert.tp3}"
                )
        else:
            print("RADAR: no qualifying setup at this snapshot; this is a valid result.")
        print("SUCCESS: public WEEX candles loaded and live radar evaluation completed.")
    finally:
        market_data.close()


if __name__ == "__main__":
    main()
