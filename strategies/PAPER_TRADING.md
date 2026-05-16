# Paper trading — quick reference

Cheat sheet for everything in `tradepro_strategies/paper/`. Run from the
`strategies/` directory.

If anything below errors with "command not found", re-install the CLIs:
```bash
cd ~/sourcecode/tradepro/tradepro/strategies
uv pip install -e .
```

---

## One-time setup

### Required for everything
Already there from the rest of TradePro — `~/.tradepro/credentials` should
contain `api_base_url` + `api_token` for the `--push` flag to work.

### Optional env vars (set in `~/.zshrc` and `source ~/.zshrc`)

| Variable | Purpose |
|---|---|
| `TRADEPRO_FINNHUB_API_KEY` | Finnhub fallback for older intraday bars (Yahoo's 1m window is only ~30 days). Same key from GitHub Secrets works. |
| `TRADEPRO_T212_API_KEY` | Trading 212 API key. Demo by default. |
| `TRADEPRO_T212_API_SECRET` | Only if you have an older T212 key+secret pair. New accounts ship a single key. |
| `TRADEPRO_T212_MODE` | `demo` (default) or `live`. |
| `TRADEPRO_T212_ALLOW_LIVE` | Must be `1` for live orders to actually send. Without it, live-mode logs `WOULD-PLACE` and skips. |
| `TRADEPRO_IBKR_ACCOUNT` | IBKR account id (e.g. `DU1234567`). DU-prefix = paper. |
| `TRADEPRO_IBKR_ALLOW_LIVE` | Same two-key gate as T212 for non-DU accounts. |

### Install IBKR (only if you want it)
```bash
uv add ib_insync
# Run TWS or IB Gateway separately, in paper mode, on port 7497
```

---

## Single session (one specific day)

```bash
# Simulated fills against Yahoo bars — zero broker setup
uv run tradepro-paper --broker yfinance --symbol AAPL --date 2026-05-15

# Same session but route fills to your T212 demo account
uv run tradepro-paper --broker t212 --symbol AAPL --date 2026-05-15

# IBKR paper account (account starts with "DU")
uv run tradepro-paper --broker ibkr --symbol AAPL --account DU1234567

# Replay against deterministic in-memory bars (no network, for testing)
uv run tradepro-paper --broker replay --symbol AAPL --date 2026-05-15
```

### Custom params
```bash
uv run tradepro-paper --broker yfinance --symbol AAPL --date 2026-05-15 \
  --range-minutes 30 \
  --risk-per-trade-usd 200 \
  --max-position-value-usd 25000
```

---

## Both brokers at once (shadow / dispatch)

```bash
# SHADOW: same orders → both brokers. Ledger gets per-broker books
# named "<strategy>.<broker>" for side-by-side fill reconciliation.
uv run tradepro-paper --broker t212,ibkr --multi-mode shadow \
  --symbol AAPL --date 2026-05-15 --account DU1234567

# DISPATCH: different strategies route to different brokers
# (configured via Python today; CLI dispatch wiring is future work).
```

---

## Walk-forward backtest (date range, one strategy)

```bash
# 30-day backtest of default ORB on AAPL
uv run tradepro-paper-backtest --symbol AAPL \
  --from 2026-04-15 --to 2026-05-15

# Single point-in-time backtest (= one specific past session)
uv run tradepro-paper-backtest --symbol AAPL --date 2026-05-10

# Include per-session details (otherwise summary only)
uv run tradepro-paper-backtest --symbol AAPL \
  --from 2026-04-15 --to 2026-05-15 --include-sessions

# List all registered strategies
uv run tradepro-paper-backtest --list-strategies

# Pick a non-default strategy
uv run tradepro-paper-backtest --symbol AAPL \
  --from 2026-04-15 --to 2026-05-15 \
  --strategy orb \
  --param range_minutes=30 \
  --param risk_per_trade_usd=200

# Ad-hoc strategy class not in the registry
uv run tradepro-paper-backtest --symbol AAPL \
  --from 2026-04-15 --to 2026-05-15 \
  --strategy-class my_pkg.strategies:MyMomentumStrategy

# Push result to the dashboard
uv run tradepro-paper-backtest --symbol AAPL \
  --from 2026-04-15 --to 2026-05-15 --push
```

---

## Compare multiple strategies (side-by-side)

Each `--entry` is `Label::strategy_name[?param=value&...]`. Repeatable.

```bash
# Compare two ORB tunings on AAPL
uv run tradepro-paper-compare --symbol AAPL \
  --from 2026-04-15 --to 2026-05-15 \
  --entry "ORB-15::orb?range_minutes=15" \
  --entry "ORB-30::orb?range_minutes=30"

# Three tunings + push to dashboard
uv run tradepro-paper-compare --symbol AAPL \
  --from 2026-04-15 --to 2026-05-15 \
  --entry "Tight::orb?range_minutes=15&risk_per_trade_usd=100" \
  --entry "Standard::orb?range_minutes=30&risk_per_trade_usd=200" \
  --entry "Wide::orb?range_minutes=60&risk_per_trade_usd=400" \
  --push

# Custom strategy via dotted path
uv run tradepro-paper-compare --symbol AAPL \
  --from 2026-04-15 --to 2026-05-15 \
  --entry "ORB::orb" \
  --entry "Mine::my_pkg.strategies:MyMomentumStrategy"
```

After `--push`, open <http://16.60.201.137/paper-backtest> → the report
appears in the left panel, click for scoreboard + equity curve.

---

## Writing your own strategy

```python
# Drop into ~/sourcecode/tradepro/tradepro/strategies/tradepro_strategies/paper/strategies/my_strategy.py
from dataclasses import dataclass
from ..registry import register_strategy
from ..strategy import Bar, Order, OrderSide, OrderType, Strategy


@register_strategy("my_momentum")
@dataclass
class MyMomentumStrategy(Strategy):
    @staticmethod
    def default_params() -> dict:
        return {"lookback": 20, "risk_per_trade_usd": 100}

    def on_session_start(self, session_date):
        self._state.clear()
        self.remember("closes", [])

    def on_bar(self, bar: Bar) -> list[Order]:
        closes = self.recall("closes")
        closes.append(bar.close)
        if len(closes) < self._params()["lookback"]:
            return []
        # ... emit orders based on momentum signal ...
        return []

    def _params(self):
        return {**self.default_params(), **(self.params or {})}
```

Then:
```bash
uv run tradepro-paper-backtest --strategy my_momentum --symbol AAPL --date 2026-05-15
```

Three ways to register, all converge on the same lookup:
1. `@register_strategy("name")` decorator (above) — for in-tree strategies
2. setuptools entry point in your own pip package's `pyproject.toml`:
   ```toml
   [project.entry-points."tradepro.strategies"]
   my_name = "my_pkg.module:MyClass"
   ```
3. `--strategy-class module:Class` for ad-hoc one-off files

---

## Where the cache lives
```
~/.tradepro/cache/intraday/<symbol>/<interval>/<YYYY-MM-DD>.parquet
```
First backtest of a session pays the Yahoo cost; every subsequent run
(different params, different strategy, comparison runs) hits the cache
and skips the network.

Clear the whole cache:
```bash
rm -rf ~/.tradepro/cache/intraday
```

Clear one symbol:
```bash
rm -rf ~/.tradepro/cache/intraday/AAPL
```

---

## Dashboard

<http://16.60.201.137/paper-backtest>

Reports appear after you `--push` from any of the CLIs. Newest-first list
on the left, click for per-strategy table + equity curve. Reports survive
until the API container restarts (in-memory store today).
