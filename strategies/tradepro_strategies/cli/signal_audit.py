"""tradepro-signal-audit — Signal vs Position audit + the HONEST NLV P&L.

Two blind spots this surfaces (both caught by the trader on the live book):

1. EXITS NOT FIRING. For each strategy we read the BROKER-GOLDEN held positions
   and re-run the trader's STATEFUL Ichimoku signal (_equity_trader_signal) on
   each held name. If the signal says FLAT (exit) but we're still holding, that's
   an `exit_overdue` — a loser whose exit fired days ago and never executed
   (STRL/STX/AMKR...). If a held name isn't in the bar cache we're `blind` — we
   can't even evaluate its exit.

2. P&L THAT HIDES REALIZED LOSSES. The desk's P&L shows unrealised-on-held, which
   can read ~flat while the ACCOUNT is down. We anchor to NLV-vs-starting-capital
   so realized churn/cost/FX losses (the −£2k on the T212 control) can't hide:
     total = nlv − start ;  realized_and_costs = total − unrealised_on_held

    uv run tradepro-signal-audit --strategy ichimoku_equity --json
    uv run tradepro-signal-audit --strategy ichimoku_equity --push
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import logging
import os

from ..ticker_renames import canonical_ticker

log = logging.getLogger("tradepro.signal_audit")

# strategy → how to read its golden book + its starting capital.
_STRATEGIES = {
    "ichimoku_equity": {
        "broker": "T212_DEMO", "source": "t212", "start_capital": 50_000.0, "ccy": "GBP",
        "universes": ["large_50", "high_beta"],   # for missed-BUY detection
    },
    "ichimoku_equity_ibkr": {
        "broker": "IBKR_PAPER", "source": "account-state", "start_capital": 1_000_000.0, "ccy": "USD",
        "universes": ["large_50"],
    },
}


def _load_daily(cache_dir: str, sym: str):
    import pandas as pd

    files = sorted(glob.glob(f"{cache_dir}/{sym}/1d/*.parquet"))
    if not files:
        return None
    df = pd.concat([pd.read_parquet(f) for f in files])
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    try:
        df.index = df.index.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.rename(columns={"high": "High", "low": "Low", "close": "Close"})


def _signal(df):
    """(position 0/1, last exit-flip date or None). None position if too short."""
    from ..paper.strategies._equity_trader_signal import (
        compute_indicators, compute_position, MIN_BARS,
    )
    if df is None or len(df) < MIN_BARS:
        return None, None
    pos = compute_position(compute_indicators(df))["position"]
    cur = float(pos.iloc[-1])
    flat = pos[(pos == 0) & (pos.shift() != 0)]
    last_exit = flat.index[-1].date().isoformat() if len(flat) else None
    return cur, last_exit


def _entry_gate_verdict(df, cfg_gates: dict) -> tuple[bool, str]:
    """Would the STRATEGY actually buy this, or do its entry gates refuse?

    The audit used to ask only `position == 1.0` and call everything else a
    MISSED BUY. That ignores every gate the live strategy applies, so a name it
    would correctly REFUSE as a blow-off top was reported as an opportunity we
    fumbled. On 4 Sep 2026 that produced 47 red rows against 8 held — a count
    nobody can act on, which is how a panel trains you to stop reading it.

    The gates are the ones in runtime_config, and the formulas are copied from
    ichimoku_equity's own meta block so the two cannot disagree:

        entry_rsi_max        RSI(14) simple-mean, as the strategy computes it
        entry_max_ext_pct    % above the 200-SMA
        entry_max_kijun_atr  (close - kijun) / ATR(14), kijun = 32-bar mid

    Returns (would_buy, why). `why` is empty when it would buy — that row is a
    genuine miss and the only kind worth acting on.
    """
    import pandas as pd
    try:
        close, high, low = df["Close"], df["High"], df["Low"]
        last = float(close.iloc[-1])

        ext_max = cfg_gates.get("entry_max_ext_pct")
        if ext_max is not None and len(close) >= 200:
            sma200 = float(close.tail(200).mean())
            if sma200 > 0:
                ext = (last / sma200 - 1.0) * 100.0
                if ext > float(ext_max):
                    return False, f"{ext:+.0f}% over 200-SMA > {ext_max}% cap"

        rsi_max = cfg_gates.get("entry_rsi_max")
        if rsi_max is not None and len(close) >= 15:
            d = close.diff()
            up = float(d.clip(lower=0).tail(14).mean())
            dn = float((-d.clip(upper=0)).tail(14).mean())
            rsi = 100.0 - 100.0 / (1.0 + up / dn) if dn > 0 else (100.0 if up > 0 else 50.0)
            if rsi > float(rsi_max):
                return False, f"RSI {rsi:.0f} > {rsi_max} cap"

        kj_max = cfg_gates.get("entry_max_kijun_atr")
        if kj_max is not None and len(close) >= 33:
            kv = (float(high.tail(32).max()) + float(low.tail(32).min())) / 2.0
            pc = close.shift(1)
            tr = pd.concat([(high - low), (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
            atr = float(tr.tail(14).mean())
            if kv > 0 and atr > 0:
                dist = (last - kv) / atr
                if dist > float(kj_max):
                    return False, f"{dist:.1f} ATR above kijun > {kj_max} cap"
    except Exception:  # noqa: BLE001
        # A gate we cannot evaluate must not silently pass the name as a miss.
        return True, "gates not evaluable"
    return True, ""


def _fetch_book(base: str, headers: dict, cfg: dict) -> tuple[list[dict], dict]:
    """Return (positions, account) from the broker golden source.
    positions: [{symbol, qty, entry, price}]; account: {nlv, cash, invested_cost, unrealised}."""
    import requests

    if cfg["source"] == "t212":
        pj = requests.get(f"{base}/api/integrations/trading212/positions",
                          headers=headers, timeout=25).json()
        cj = requests.get(f"{base}/api/integrations/trading212/cash",
                          headers=headers, timeout=25).json()
        # Canonicalise corporate-action renames (broker reports LB/FB; the
        # universe/signal/bar-cache use BBWI/META) so held names match the
        # signal — else BBWI shows as a "missed buy" and LB/FB read "blind".
        positions = [
            {"symbol": canonical_ticker(p.get("yahooSymbol")), "qty": p.get("quantity") or 0,
             "entry": p.get("averagePricePaid"), "price": p.get("currentPrice")}
            for p in pj.get("positions", [])
        ]
        account = {
            "nlv": cj.get("total"), "cash": cj.get("free"),
            "invested_cost": cj.get("invested"), "unrealised": cj.get("ppl"),
        }
        return positions, account

    # account-state (IBKR): whole-account book + NLV
    aj = requests.get(f"{base}/api/integrations/account-state", headers=headers, timeout=25).json()
    acct = next((a for a in aj.get("accounts", []) if a.get("broker") == cfg["broker"]), None)
    if acct is None:
        return [], {}
    # equity only (skip options: 100x multiplier heuristic) for the signal audit
    positions = []
    inv_cost = 0.0
    for p in acct.get("positions", []):
        q = p.get("qty") or 0
        if q <= 0:
            continue
        mv, mark = p.get("marketValue"), p.get("mark")
        is_opt = mark and mv and q and abs(abs(mv) / (abs(q) * mark) - 100) < 10
        if is_opt:
            continue
        inv_cost += (p.get("avgCost") or 0) * q
        positions.append({"symbol": canonical_ticker(p.get("symbol")), "qty": q,
                          "entry": p.get("avgCost"), "price": p.get("mark")})
    account = {"nlv": acct.get("netLiquidation"), "cash": acct.get("totalCash"),
               "invested_cost": inv_cost, "unrealised": acct.get("unrealisedPnl")}
    return positions, account


def audit(strategy: str, base: str, headers: dict, cache_dir: str) -> dict:
    cfg = _STRATEGIES[strategy]
    positions, account = _fetch_book(base, headers, cfg)

    rows = []
    for p in positions:
        sym, qty, entry, price = p["symbol"], p["qty"], p["entry"], p["price"]
        pos, last_exit = _signal(_load_daily(cache_dir, sym)) if sym else (None, None)
        pnl_pct = ((price / entry - 1) * 100) if (entry and price) else None
        if pos is None:
            cls = "blind"        # held but we have no bars → can't evaluate the exit
        elif pos == 0.0:
            cls = "exit_overdue"  # signal says SELL, still held
        else:
            cls = "hold"          # signal still long — correctly held
        days_overdue = None
        if cls == "exit_overdue" and last_exit:
            d = (_dt.date.today() - _dt.date.fromisoformat(last_exit)).days
            days_overdue = d
        rows.append({
            "symbol": sym, "qty": qty,
            "entry": round(entry, 2) if entry else None,
            "price": round(price, 2) if price else None,
            "pnl_pct": round(pnl_pct, 1) if pnl_pct is not None else None,
            "classification": cls,
            "exit_fired": last_exit if cls == "exit_overdue" else None,
            "days_overdue": days_overdue,
        })

    # Missed BUYs: universe names whose signal says LONG but we're FLAT (not held) —
    # the ENTRY half of the signal-execution gap. Surfaced even when auto-entry is
    # gated off (--reconcile-entries), so the trader SEES what the strategy would buy.
    import requests as _rq
    held_syms = {p["symbol"] for p in positions if p.get("symbol")}
    # THE STRATEGY'S OWN GATES, from the same runtime_config it runs on — never
    # a second copy of the numbers here. entry_rsi_max=80, entry_max_ext_pct=50
    # and entry_max_kijun_atr=1.5 were all live and all ignored by this audit.
    gates: dict = {}
    try:
        for m in (_rq.get(f"{base}/api/admin/strategy-broker-map",
                          headers=headers, timeout=15).json().get("mappings") or []):
            if m.get("strategy_id") != strategy:
                continue
            rc = m.get("runtime_config") or m.get("runtimeConfig") or {}
            if isinstance(rc, str):
                import json as _j
                rc = _j.loads(rc)
            gates = {k: rc.get(k) for k in
                     ("entry_rsi_max", "entry_max_ext_pct", "entry_max_kijun_atr")
                     if rc.get(k) is not None}
            break
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read entry gates (%s) — every LONG will read as "
                    "a genuine miss", str(exc)[:120])
    missed_buys: list[dict] = []
    for uname in cfg.get("universes", []):
        try:
            usyms = [s["ticker"] for s in _rq.get(
                f"{base}/api/universes/{uname}", headers=headers, timeout=15
            ).json().get("symbols", []) if s.get("effective", True)]
        except Exception:  # noqa: BLE001
            usyms = []
        for us in usyms:
            if us in held_syms or any(m["symbol"] == us for m in missed_buys):
                continue
            df = _load_daily(cache_dir, us)
            pos, _ = _signal(df)
            if pos != 1.0:
                continue
            # LONG and flat — but would the strategy actually BUY it? Apply its
            # own entry gates. A name refused as too extended is the strategy
            # WORKING, not an opportunity missed, and lumping the two together
            # is what turned this panel into 47 unactionable red rows.
            would, why = _entry_gate_verdict(df, gates)
            missed_buys.append({"symbol": us, "universe": uname,
                                "would_enter": would,
                                "blocked_by": why or None})
    missed_buys.sort(key=lambda m: m["symbol"])

    # Honest P&L: NLV vs start, splitting realized+costs from unrealised-on-held.
    #
    # START CAPITAL IS CONFIG, NOT A CONSTANT (fixed 21 Aug 2026). It was
    # hardcoded at 1_000_000 for the IBKR sleeve. The owner then RESET the paper
    # account and deliberately brought the balance down to ~151k — and this
    # audit went on subtracting from the old figure, reporting
    #
    #     total_pnl -848,807.88   total_pnl_pct -84.88%
    #
    # i.e. it published a deliberate balance change as a catastrophic TRADING
    # LOSS, on the surface whose entire job is honest P&L. Meanwhile the sleeve's
    # three open positions were +2.7%, -0.4% and +14.2% and its recent realised
    # was about -$442. Nothing about -84.88% was true.
    #
    # Env override first (TRADEPRO_START_CAPITAL_<STRATEGY>), then the table.
    _env_key = f"TRADEPRO_START_CAPITAL_{strategy.upper()}"
    start = float(os.environ.get(_env_key) or cfg["start_capital"])
    nlv = account.get("nlv")
    unreal = account.get("unrealised")
    total_pnl = (nlv - start) if nlv is not None else None
    realized_and_costs = (total_pnl - unreal) if (total_pnl is not None and unreal is not None) else None

    # BASELINE SANITY. A P&L this large has to be explainable by trades. If the
    # implied realised loss exceeds what the whole account could plausibly have
    # traded away, the baseline is stale (an account reset, a re-fund, a broker
    # switch) — and reporting it as performance is a false positive of exactly
    # the kind the standing rule forbids. Say "baseline is wrong" instead of
    # publishing a number that is not a loss.
    baseline_suspect = False
    baseline_note = None
    if total_pnl is not None and start > 0 and nlv is not None:
        if total_pnl < 0 and abs(total_pnl) > 0.5 * start and nlv > 0.05 * start:
            baseline_suspect = True
            baseline_note = (
                f"START CAPITAL LOOKS STALE: configured {start:,.0f} vs current NLV "
                f"{nlv:,.0f} implies {total_pnl:,.0f} of realised loss, which is more "
                f"than half the starting balance while the account is still solvent and "
                f"open positions are healthy. That pattern is an account RESET or "
                f"re-fund, not trading. P&L is NOT reported until the baseline is "
                f"corrected — set {_env_key} or fix the table."
            )
            log.warning("%s: %s", strategy, baseline_note)

    def n(c): return sum(r["classification"] == c for r in rows)
    overdue = [r for r in rows if r["classification"] == "exit_overdue"]
    overdue.sort(key=lambda r: r["pnl_pct"] if r["pnl_pct"] is not None else 0)

    return {
        "kind": "signal_audit", "strategy": strategy, "broker": cfg["broker"],
        "currency": cfg["ccy"],
        "as_of_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "pnl": {
            "start_capital": start, "nlv": nlv, "cash": account.get("cash"),
            "invested_cost": account.get("invested_cost"),
            "unrealised_on_held": unreal,
            # Suppressed rather than published when the baseline is not credible.
            "total_pnl": None if baseline_suspect else total_pnl,
            "realized_and_costs": None if baseline_suspect else realized_and_costs,
            "total_pnl_pct": (None if baseline_suspect else
                              (round(total_pnl / start * 100, 2) if total_pnl is not None else None)),
            "baseline_suspect": baseline_suspect,
            "baseline_note": baseline_note,
        },
        "counts": {"held": len(rows), "hold": n("hold"),
                   "exit_overdue": n("exit_overdue"), "blind": n("blind"),
                   # The headline is the ACTIONABLE count. A name the entry
                   # gates refuse is the strategy working; counting it as a
                   # miss is what made this panel unreadable.
                   "missed_buys": sum(1 for m in missed_buys if m.get("would_enter")),
                   "gate_blocked": sum(1 for m in missed_buys if not m.get("would_enter"))},
        "exit_overdue": overdue,
        "missed_buys": missed_buys,
        "blind": [r["symbol"] for r in rows if r["classification"] == "blind"],
        "positions": rows,
        "note": ("Signal = trader's stateful Ichimoku (exit when Close<cloud_bottom OR "
                 "tenkan<kijun). exit_overdue = signal says SELL but still held. missed_buys "
                 "= signal says LONG but we're flat (entry gap). P&L is NLV-vs-start "
                 "(realized+costs NOT hidden). blind = no bars."),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(prog="tradepro-signal-audit")
    p.add_argument("--strategy", default="ichimoku_equity", choices=list(_STRATEGIES))
    p.add_argument("--api-base", default=None)
    p.add_argument("--cache-dir", default=os.path.expanduser("~/.tradepro/bar_cache/us_etf"))
    p.add_argument("--json", action="store_true")
    p.add_argument("--push", action="store_true")
    args = p.parse_args()

    from . import push_to_api as _pta
    base, token = args.api_base, None
    if not base:
        base, token = _pta.load_credentials()
    base = base.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    art = audit(args.strategy, base, headers, args.cache_dir)

    pnl = art["pnl"]
    log.info("%s: NLV %s vs start %s = total %s (%s%%) | held-unrealised %s | realized+costs %s",
             args.strategy, pnl["nlv"], pnl["start_capital"], pnl["total_pnl"],
             pnl["total_pnl_pct"], pnl["unrealised_on_held"], pnl["realized_and_costs"])
    log.info("counts: %s", art["counts"])
    for r in art["exit_overdue"]:
        log.info("  EXIT-OVERDUE %-6s entry=%s now=%s %s%% fired=%s (%s d overdue)",
                 r["symbol"], r["entry"], r["price"], r["pnl_pct"], r["exit_fired"], r["days_overdue"])
    if art["blind"]:
        log.info("  BLIND (no bars): %s", art["blind"])

    if args.json:
        import json
        print(json.dumps(art, indent=2))
    if args.push:
        if not token:
            _, token = _pta.load_credentials()
        _pta.push("signal-audit", {"strategy": args.strategy, "label": "latest",
                  "uploaded_by": os.uname().nodename, "artifact": art}, base, token)
        log.info("pushed signal-audit for %s", args.strategy)
        try:
            from ..run_log import log_run
            c = art["counts"]
            log_run("signal-audit", "audit", "ok", base=base, token=token,
                    summary=(f"{args.strategy}: {c.get('exit_overdue',0)} exit-overdue, "
                             f"{c.get('missed_buys',0)} missed-buys, {c.get('blind',0)} blind"))
        except Exception:  # noqa: BLE001 — heartbeat must never fail the run
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
