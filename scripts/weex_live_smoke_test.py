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


TIMEFRAME_MS = {"1d": 86_400_000, "4h": 14_400_000, "1h": 3_600_000, "15m": 900_000, "5m": 300_000}\nTIMEFRAMES = tuple(TIMEFRAME_MS)


def main() -> None:
    market_data = WeexMarketData(exchange_id="weex", quote_currency="USDT")
    try:
        candidate = market_data.resolve_manual_symbol("BTC")
        print(f"Resolved public contract: {candidate.symbol}")
        print(f"Last price: {candidate.last_price}")

        failures: list[str] = []
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        for timeframe in TIMEFRAMES:
            try:
                rows = market_data.fetch_ohlcv(candidate.symbol, timeframe, limit=100)
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
        print("SUCCESS: all five public WEEX candle timeframes are readable.")
    finally:
        market_data.close()


if __name__ == "__main__":
    main()
