"""Read-only WEEX public market-data smoke test.

This script uses only public market endpoints through the same CCXT adapter
as the bot. It never authenticates or places orders.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from app.market_data import WeexMarketData


TIMEFRAMES = ("1d", "4h", "1h", "15m", "5m")


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

                latest = valid[-1]
                age_minutes = max(0, (now_ms - int(latest[0])) // 60_000)
                print(
                    f"PASS {timeframe}: rows={len(valid)}, "
                    f"latest_close={latest[4]}, latest_candle_age_min={age_minutes}"
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
