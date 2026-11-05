# Deploying on HOKAS

This service runs a Python API, the Telegram polling bot, and the public-market scanner in one process. It is demo-only and must not be configured with exchange trading credentials.

## HOKAS server settings

1. Use a **Python Server** workload (not a Telegram-only template), because the process also serves the read-only HTTP API.
2. Connect this repository/branch: `aliasadollahi3364-ops/orderblock-signal-bot`, `main`.
3. Use Python 3.12 if the panel asks for a runtime version.
4. Startup command: `python main.py`.
5. The app binds to `0.0.0.0` and reads `PORT` (default `8000`).

## Environment variables

Set these in the HOKAS server's environment/settings panel. Do not commit Telegram credentials to GitHub.

| Variable | Initial value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token |
| `TELEGRAM_CHAT_ID` | Your private Telegram chat ID |
| `DATABASE_PATH` | `./data/demo.sqlite3` |
| `DEMO_INITIAL_BALANCE` | `100` |
| `DEMO_MARGIN_PER_TRADE` | `5` |
| `DEMO_LEVERAGE` | `5` |
| `SCANNER_UNIVERSE_LIMIT` | `50` (start small on free resources) |
| `SCANNER_CANDIDATE_LIMIT` | `10` |
| `SCAN_INTERVAL_SECONDS` | `300` |
| `LIVE_TRADING_ENABLED` | `false` |
| `HOST` | `0.0.0.0` |
| `PORT` | `8000` unless HOKAS supplies one |

The free HOKAS tier is listed as 0.3 vCPU, 256 MB RAM and 512 MB disk. Begin with the conservative scanner values above and inspect RAM, CPU, logs and scan duration before increasing the universe/candidate limits. The project defaults of 500 markets and 60 candidates may be too demanding for the free tier. Increase gradually (for example 50/10, then 100/15) only if the service remains stable. The strategy rules are unchanged by these scanner-capacity settings.

## SQLite data and backups

The database is created at `DATABASE_PATH`; the parent directory is created automatically. Keep it in the server's writable project directory and do not place it in a temporary build/cache directory.

HOKAS documents a Backups area in server management, but its public documentation does not specify a persistent-volume guarantee for application files across every redeploy/rebuild. Therefore:

- Create a HOKAS backup before replacing/redeploying the server.
- After first startup, verify the database exists with `ls -lah data/` in the terminal.
- Create and periodically download/retain a backup using the HOKAS Backups feature.
- Renew the free server before its time expires. HOKAS documents that an expired server stops and is automatically deleted after three days; its files cannot then be restored.
- Do not assume a successful process restart proves data survives server replacement. Verify with a backup/restore test before relying on persistence.

## Smoke checks after starting

Open these paths on the server's public URL:

- `/health` — should return `ok: true`, `mode: DEMO_ONLY`, `live_trading: false`.
- `/api/status` — should report scanner state and `live_trading_enabled: false`.
- `/api/account` — should show the configured 100 USDT demo balance, 5 USDT margin and 5x leverage.

Then send `/status` and `/demo` to the configured Telegram bot. Check HOKAS logs for exchange/rate-limit errors and memory use.

## Safety

- Never set `LIVE_TRADING_ENABLED=true`.
- Do not add WEEX API keys or private exchange credentials. The market-data adapter uses public endpoints only.
- This guide is preparation only; deployment and account/server changes require the owner's explicit action.
