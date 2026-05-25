# TradePro — strategies

Python research package. This is where you iterate on ideas quickly, run
heavier simulations, cache data, and (later) train models. The .NET API has a
lighter event-driven simulator that mirrors this one for on-demand UI flows;
use Python when you want to sweep a parameter grid or crunch hundreds of
symbols.

## Why Python on the M4

- Pandas + numpy are fast enough that a full FTSE-350 daily backtest finishes
  in seconds.
- `torch` ships with MPS (Apple Silicon) acceleration — when you add neural
  models later, set `device = "mps"` and you're done.
- No GPU hosting bill. Run locally, push a results JSON to the API or
  Firestore.

## Setup with uv

This project uses [uv](https://docs.astral.sh/uv/) — a single static binary
that replaces `pip`, `pip-tools`, `pipx`, `virtualenv`, and `pyenv`. It's
10-100× faster than pip and handles Python version pinning for you.

### One-time install (Mac)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

(Or `brew install uv`.)

### Install the project

```bash
cd strategies
uv sync                 # creates .venv and installs all deps from uv.lock
uv sync --extra ml      # also installs scikit-learn / lightgbm / torch
```

`uv.lock` is committed — `uv sync` is fully reproducible across machines.

### Run a backtest

```bash
# UK large-cap buy and hold
uv run tradepro-backtest --symbol BARC.L --strategy buy_and_hold \
    --from 2019-01-01 --capital 10000

# SMA crossover on the FTSE 100
uv run tradepro-backtest --symbol ^FTSE --strategy sma_crossover \
    --fast 20 --slow 50 --from 2015-01-01

# Write results JSON for the API to ingest later
uv run tradepro-backtest --symbol BP.L --strategy sma_crossover \
    --out ../out/bp_sma.json
```

### Push results to the website

```bash
uv run tradepro-push --kind backtest ../out/bp_sma.json
```

### Ad-hoc Python

```bash
uv run python                 # interpreter with the venv active
uv run ipython                # if installed via --extra dev
uv run pytest                 # the dev group includes pytest
```

### Add a dependency

```bash
uv add polars                 # adds to pyproject.toml and uv.lock
uv add --group ml xgboost     # adds to the optional "ml" group
uv remove matplotlib          # removes cleanly
```

## Layout

```
tradepro_strategies/
  data.py                      yahoo / stooq / binance loaders (matches backend)
  indicators.py                SMA / EMA / RSI / MACD (vectorised)
  backtest.py                  event loop, UK fee model, equity/PnL stats
  strategies/
    buy_and_hold.py
    sma_crossover.py
  cli/
    run_backtest.py            exposed as `tradepro-backtest`
    push_to_api.py             exposed as `tradepro-push`
pyproject.toml                 project metadata + dependencies
uv.lock                        pinned dependency graph (commit this)
```

## Local cache + worker on the Mac

```bash
# Refresh the local Parquet cache (idempotent, run nightly).
uv run tradepro-refresh --watchlist uk --years 10

# Start the Firestore-driven job worker. Picks up backtest requests
# submitted from the web UI and runs them locally. Ctrl-C to stop.
uv run tradepro-worker
```

### Worker credentials

The worker needs a Firebase Admin service-account key at
`~/.tradepro/firebase-sa.json`:

```bash
mkdir -p ~/.tradepro && chmod 700 ~/.tradepro
# Drop the JSON downloaded from Firebase → Project settings → Service accounts
mv ~/Downloads/smsp-291e3-firebase-adminsdk-*.json ~/.tradepro/firebase-sa.json
chmod 600 ~/.tradepro/firebase-sa.json
```

Every run produces:
- `~/.tradepro/logs/<date>/<run_id>.jsonl` — structured event log.
- `~/.tradepro/artefacts/<run_id>/` — `manifest.json`, `equity_curve.parquet`,
  `trades.parquet`.
- A Firestore doc under `jobs/{id}` / `runs/{id}` that the UI reads.

## Pushing results to the website

The web UI reads from the Azure-hosted API; the API reads from a database
(Firestore, on free tier). Your Mac never accepts inbound connections —
instead it pushes.

```bash
# 1. Store creds once (chmod 600 so others on the Mac can't read it)
mkdir -p ~/.tradepro && chmod 700 ~/.tradepro
cat > ~/.tradepro/credentials <<'JSON'
{ "api_base_url": "https://tradepro-api-g2ardxhffph4fbdr.canadacentral-01.azurewebsites.net",
  "api_token":    "<matches Ingest__Token on Azure>" }
JSON
chmod 600 ~/.tradepro/credentials

# 2. Run a backtest that writes JSON
uv run tradepro-backtest --symbol BARC.L --strategy sma_crossover \
    --out ../out/barc_sma.json

# 3. Push the result to the API (retries with exponential backoff)
uv run tradepro-push --kind backtest ../out/barc_sma.json
```

Automate with `launchd` — drop a plist into `~/Library/LaunchAgents/` that
runs the command every evening after the US close.

## Paper trading

Run a live paper session against T212 demo manually:

```bash
# Equity — Ichimoku trend-following (daily, MOO signal)
uv run tradepro-paper --broker t212 --strategy ichimoku_equity \
    --symbols AAPL,MSFT,NVDA,TSLA --capital-usd 100000 \
    --placement-mode manual --push

# FX — Ichimoku mean-reversion (hourly, all G10 pairs)
# Safe to run on weekends / UK bank holidays — FX is 24/5
uv run tradepro-paper --broker t212 --strategy ichimoku_fx_mr \
    --capital-usd 50000 --placement-mode manual --push
```

`--placement-mode manual` queues orders for human Approve/Reject in the UI.
`--push` sends the session snapshot to the API so the Paper page updates.

### Automatic scheduling (Mac launchd)

The `scripts/launchd/` folder contains plists that fire the above commands
automatically. Install them once:

```bash
# From the repo root (tradepro/tradepro/), NOT from inside strategies/
bash scripts/install_paper_schedules.sh
```

This loads three launchd jobs:

| Job | Schedule | What it runs |
|-----|----------|--------------|
| `com.tradepro.paper-equity` | Weekdays 13:35 UTC (8:35 ET) | `ichimoku_equity` on 10 US names |
| `com.tradepro.paper-fx` | Weekdays 22:05 UTC (6:05 ET) | `ichimoku_fx_mr` on all G10 pairs |
| `com.tradepro.paper-watch` | Every 2 min | Polls for UI-triggered sessions |

Check logs:

```bash
tail -f /tmp/tradepro-paper-equity.log
tail -f /tmp/tradepro-paper-fx.log
tail -f /tmp/tradepro-paper-watch.log
```

Uninstall:

```bash
launchctl unload ~/Library/LaunchAgents/com.tradepro.paper-*.plist
```

### UI trigger

Open the Paper page in the web UI (`/paper-live`), pick a strategy, and click
**Run Session**. The Mac daemon (`tradepro-paper-watch`) picks it up within
60 seconds and runs the session.

---

## Adding a strategy

1. Create `tradepro_strategies/strategies/<name>.py` exporting a function that
   takes a price DataFrame and returns a signal Series (+1 / -1 / 0).
2. Add it to `STRATEGIES` in `tradepro_strategies/cli/run_backtest.py`.
3. Keep it pure and vectorised — no mutable global state.
