# TradePro — strategies

Python research package. This is where you iterate on ideas quickly, run
heavier simulations, and (later) train models. The .NET API has a lighter
event-driven simulator that mirrors this one for the on-demand UI flow; use
Python when you want to sweep a parameter grid or crunch hundreds of symbols.

## Why Python on the M4

- Pandas + numpy are fast enough that a full FTSE-350 daily backtest finishes
  in seconds.
- `torch` ships with MPS (Apple Silicon) acceleration — when you add neural
  models later, set `device = "mps"` and you're done.
- No GPU hosting bill. Run locally, push a results JSON to the API.

## Setup

```bash
cd strategies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# For Phase 4 ML work:
# pip install -r requirements-ml.txt
```

## Run a backtest

```bash
# UK large-cap buy and hold
python scripts/run_backtest.py --symbol BARC.L --strategy buy_and_hold \
    --from 2019-01-01 --capital 10000

# SMA crossover on the FTSE 100 index
python scripts/run_backtest.py --symbol ^FTSE --strategy sma_crossover \
    --fast 20 --slow 50 --from 2015-01-01

# Write results JSON so the API can serve it later
python scripts/run_backtest.py --symbol BP.L --strategy sma_crossover \
    --out ../out/bp_sma.json
```

## Layout

```
tradepro_strategies/
  data.py              yahoo / stooq / binance loaders (matches backend)
  indicators.py        SMA / EMA / RSI / MACD (vectorised)
  backtest.py          event loop, UK fee model, equity/PnL stats
  strategies/
    buy_and_hold.py
    sma_crossover.py
scripts/
  run_backtest.py      CLI entry point
```

## Adding a strategy

1. Create `tradepro_strategies/strategies/<name>.py` exporting a function that
   takes a price DataFrame and returns a signal Series (+1 / -1 / 0).
2. Add it to `STRATEGIES` in `scripts/run_backtest.py`.
3. Keep it pure and vectorised — no mutable global state.
