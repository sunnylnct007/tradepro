"""V4 sell-off exits — runs exactly the pre-registered set (c462427f)."""
import json, warnings
warnings.filterwarnings("ignore")
import logging; logging.disable(logging.WARNING)
from tradepro_strategies.momentum_rotation import backtest, with_overrides, cached_panels, _stats

VARIANTS = [("control", {}), ("spy200_daily", {"market_exit": "spy200_daily"}),
            ("spy200_buffer3", {"market_exit": "spy200_buffer3"}),
            ("spy200_weekly", {"market_exit": "spy200_weekly"}),
            ("spy50_daily", {"market_exit": "spy50_daily"})]
WINDOWS = [("2008-09-01", "2009-03-31"), ("2011-07-01", "2011-10-31"), ("2015-08-01", "2016-02-29"),
           ("2018-10-01", "2018-12-31"), ("2020-02-01", "2020-04-30"), ("2022-01-01", "2022-10-31"),
           ("2026-11-13", None)]
P = cached_panels(None)
rows = []
for name, ov in VARIANTS:
    full = backtest(with_overrides(**ov), P)
    h1 = backtest(with_overrides(**ov, end="2017-12-31"), P)
    h2 = backtest(with_overrides(**ov, start="2018-01-01"), P)
    nav = full["nav"]
    win = {}
    for a, b in WINDOWS:
        s = nav.loc[a:b] if b else nav.loc[a:]
        win[a[:7]] = round((s.iloc[-1] / s.iloc[0] - 1) * 100, 1) if len(s) > 1 else None
    fa = full["metrics"]["account"]
    rows.append({"variant": name, "cagr": fa["cagr_pct"], "dd": fa["max_drawdown_pct"], "sharpe": fa["sharpe"],
                 "h1": (h1["metrics"]["account"]["sharpe"], h1["metrics"]["account"]["cagr_pct"]),
                 "h2": (h2["metrics"]["account"]["sharpe"], h2["metrics"]["account"]["cagr_pct"]),
                 "trades": full["activity"]["trades"], "market_exits": full["activity"].get("market_exits"),
                 "cash_pct": full["activity"]["time_in_cash_pct"], "windows": win,
                 "end": str(nav.index[-1])[:10]})
    print(json.dumps(rows[-1]), flush=True)
c = rows[0]
for r in rows[1:]:
    ok = (r["h1"][0] >= c["h1"][0] + 0.05 and r["h1"][1] >= c["h1"][1] and
          r["h2"][0] >= c["h2"][0] + 0.05 and r["h2"][1] >= c["h2"][1] and r["dd"] >= c["dd"] - 2)
    print("PASS" if ok else "fail", r["variant"])
