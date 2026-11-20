"""Read-only multi-symbol WEEX radar smoke test.

The explicit whitelist below is a subset of the user's manually-added symbols.
Only public market data is requested; no credentials, Telegram messages,
demo positions, or exchange orders are used.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

# Running a script from scripts/ puts that directory first on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.market_data import WeexMarketData
from app.explosive_scanner import DURATION_MS, _closed_candles, _ob_for_side
from app.market_radar import evaluate_market_radar


# This eighth batch is drawn only from the user's current manual-only list.
MANUAL_SYMBOLS = (
    "ETC", "ETHFI", "FIL", "HBAR", "IMX", "JASMY", "LDO", "LIT",
)
LIMITS = {"1d": 400, "4h": 220, "1h": 120, "15m": 320, "5m": 120}
TIMEFRAMES = tuple(LIMITS)


def valid_closed_rows(rows: list[list[float]], timeframe: str, now_ms: int) -> int:
    duration = DURATION_MS[timeframe]
    count = 0
    for row in rows:
        if len(row) < 6:
            continue
        try:
            values = [float(value) for value in row[:6]]
            if not all(math.isfinite(value) for value in values):
                continue
            if values[0] > 0 and int(values[0]) + duration <= now_ms:
                count += 1
        except (TypeError, ValueError, OverflowError):
            continue
    return count


def main() -> None:
    market_data = WeexMarketData(exchange_id="weex", quote_currency="USDT")
    tested = 0
    failed: list[str] = []
    alert_count = 0
    try:
        print("WEEX MANUAL RADAR BATCH TEST (READ ONLY)")
        print("Manual whitelist: " + ", ".join(MANUAL_SYMBOLS))
        for requested in MANUAL_SYMBOLS:
            print(f"\n=== {requested}/USDT ===")
            try:
                candidate = market_data.resolve_manual_symbol(requested)
                now_ms = int(time.time() * 1000)
                bars = {}
                for timeframe in TIMEFRAMES:
                    rows = market_data.fetch_ohlcv(
                        candidate.symbol, timeframe, limit=LIMITS[timeframe]
                    )
                    closed_count = valid_closed_rows(rows, timeframe, now_ms)
                    closed = _closed_candles(rows, timeframe, now_ms)
                    if len(closed) < 30:
                        raise RuntimeError(
                            f"{timeframe}: only {len(closed)} valid closed candles"
                        )
                    bars[timeframe] = closed
                    latest = closed[-1]
                    age_min = max(
                        0,
                        (now_ms - (latest.timestamp + DURATION_MS[timeframe])) // 60_000,
                    )
                    print(
                        f"PASS {timeframe}: raw={len(rows)}, "
                        f"closed={closed_count}, valid_closed={len(closed)}, "
                        f"last_close={latest.close}, age_min={age_min}"
                    )

                radar_price = bars["15m"][-1].close
                bull_ob, _, _ = _ob_for_side(bars["4h"], "BUY", radar_price)
                bear_ob, _, _ = _ob_for_side(bars["4h"], "SELL", radar_price)
                alerts = evaluate_market_radar(
                    candidate.symbol,
                    bars["1d"], bars["4h"], bars["1h"],
                    bars["15m"], bars["5m"],
                    bullish_ob_near=bull_ob,
                    bearish_ob_near=bear_ob,
                )
                tested += 1
                alert_count += len(alerts)
                print(
                    f"RADAR: symbol={candidate.symbol}, last={candidate.last_price}, "
                    f"bullish_4h_ob_near={bull_ob}, bearish_4h_ob_near={bear_ob}, "
                    f"alerts={len(alerts)}"
                )
                if alerts:
                    for alert in alerts:
                        print(
                            f"ALERT category={alert.category} side={alert.side} "
                            f"stage={alert.stage} score={alert.score} "
                            f"reference={alert.reference_price} "
                            f"entry={alert.entry_price} sl={alert.stop_loss} "
                            f"tp1={alert.tp1} tp2={alert.tp2} tp3={alert.tp3} "
                            f"reasons={' ; '.join(alert.reasons)}"
                        )
                else:
                    print("RADAR: no qualifying setup at this snapshot.")
            except Exception as exc:
                failed.append(f"{requested}: {type(exc).__name__}: {exc}")
                print(f"FAIL {requested}: {type(exc).__name__}: {exc}")

        print("\n=== SUMMARY ===")
        print(
            f"tested={tested}/{len(MANUAL_SYMBOLS)}, "
            f"symbols_with_errors={len(failed)}, alerts={alert_count}"
        )
        for item in failed:
            print("SYMBOL_ERROR " + item)
        if tested == 0:
            raise SystemExit("No manually-listed symbol could be tested successfully.")
        print("SUCCESS: live public WEEX radar evaluation completed.")
    finally:
        market_data.close()


if __name__ == "__main__":
    main()
