"""tradepro-preearnings-watch — Phase 1 of the pre-earnings spec: alerts only.

    uv run tradepro-preearnings-watch            # evaluate every configured symbol
    uv run tradepro-preearnings-watch --dry-run  # print, send nothing, save nothing

Implements the advisor's v1.1 revision of the MU Pre-Earnings Swing & Core
spec (6 Sep 2026) as a GENERIC engine: the framework lives here, every number
lives in settings-kv per symbol (`preearnings_cfg_MU`, `preearnings_cfg_SNDK`,
...). The owner's next spec is a config row, not a module.

## What Phase 1 is, and is not

No broker execution. No staged orders. No averaging. No options. It reads
data, tracks a state machine, DELIVERS DEDUPLICATED ALERTS, publishes its
state to the candidates board (so the latest potential order is on the SCREEN
and in the regular digest EMAIL — owner: "i see latest potential order in
screen as well as email"), and writes the forward-test journal §19.1 needs.

## The rules that keep this from becoming noise

* One alert per STATE TRANSITION, keyed (symbol, alert_id, state_detail),
  fired-keys persisted in settings-kv — a quote hovering at a level fires
  nothing twice.
* One delivery channel: the existing email sender + the existing board.
* The cycle EXPIRES at the confirmed print. Renewal is a decision (refresh
  levels, ATR, date, and owner approval), never a default.
* CONFIGURATION_BLOCKED until both risk budgets exist. An arbitrary share
  clip may not substitute for a risk budget (advisor v1.1 §1).
* A calendar disagreement (≠ exactly one confirmed future print) BLOCKS —
  the MU 21st/30th conflict is why.

## Data honesty

Daily bars: the settled store, disk-only. Intraday 15m: yfinance, labelled
`yfinance_15m` on every artifact — the alert lane must never contend for the
single IBKR market-data session. Sector proxy: SOXX (SMH is not harvested;
the spec allows a configured proxy).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
from types import SimpleNamespace
from zoneinfo import ZoneInfo

log = logging.getLogger("tradepro.preearnings_watch")

ET = ZoneInfo("America/New_York")
STRATEGY_VERSION = "PRE_EARNINGS_V1_1"

# Created in settings-kv on first run if absent — THE numbers live there.
MU_DEFAULT_CFG = {
    "symbol": "MU",
    "sector_proxy": "SOXX",
    "sector_floor_pct": -1.5,
    "relative_strength_floor_pct": 0.0,
    "reference_zones": {          # chart REFERENCE, not triggers (v1.1 §3)
        "shallow_pullback": 990.0,
        "breakout_watch": 1050.0,
        "primary_retest": [965.0, 975.0],
        "deeper_core": [935.0, 955.0],
        "profit_1": [1070.0, 1080.0],
        "profit_2": [1095.0, 1110.0],
    },
    "atr_risk": {                 # v1.1 final risk logic
        "default_stop_distance_atr": 0.8,
        "maximum_unapproved_stop_distance_atr": 1.0,
        "structural_buffer_atr": 0.1,
    },
    "dynamic_bands": {            # v1.1 §dynamic alerts
        "ema_proximity_atr": 0.25,
        "extended_from_ema_atr": 1.5,
        "gap_down_atr": 1.0,
    },
    "breakout_watch_level": 1050.0,   # owner, 6 Sep — M1 context alert
    # CONFIGURATION_BLOCKED until the owner sets BOTH (v1.1 §1).
    "max_risk_per_swing_trade_currency": None,
    "max_gap_risk_for_core_currency": None,
    "max_swing_shares": 50,
    "max_total_shares": 100,
}


# ── settings-kv helpers ───────────────────────────────────────────────────

def _kv_get(base, token, key):
    import requests
    r = requests.get(f"{base}/api/settings-kv/{key}",
                     headers={"Authorization": f"Bearer {token}"} if token else {},
                     timeout=15)
    return r.json().get("value") if r.status_code == 200 else None


def _kv_put(base, token, key, value, label, desc, create=False):
    import requests
    H = {"Authorization": f"Bearer {token}"} if token else {}
    if create:
        requests.post(f"{base}/api/settings-kv/", headers=H, timeout=15,
                      json={"key": key, "value": value, "valueType": "json",
                            "label": label, "description": desc,
                            "category": "Trading"})
    else:
        # PUT stores the ENTIRE body as the value (SettingsKvEndpoints.cs
        # takes JsonElement body raw) — wrapping in {"value": ...} double-wraps.
        requests.put(f"{base}/api/settings-kv/{key}", headers=H, timeout=15,
                     json=value)


# ── data ──────────────────────────────────────────────────────────────────

def _daily(sym):
    """Settled store first; labelled yfinance fallback for the Lambda runtime.

    Verified 6 Sep: the BarStore does NOT read through from S3 on a cold
    HOME, and Lambda's filesystem starts cold every invoke. The alert lane
    may run on Yahoo daily bars — VISIBLY (`source` rides every payload) —
    per the fallback rule; anything that sizes real risk must not.
    """
    from .preearnings_profile import _atr, _ema, _load_ohlc
    try:
        dates, o, h, l, c = _load_ohlc(sym)
        src = "bar_store"
    except SystemExit:
        from ..yahoo_session import yahoo_session
        import yfinance as yf
        df = yf.Ticker(sym, session=yahoo_session()).history(period="400d",
                                                             interval="1d")
        if df is None or df.empty:
            raise SystemExit(f"ERROR: no daily bars for {sym} from store OR yfinance")
        dates = [str(x)[:10] for x in df.index]
        o, h, l, c = (list(map(float, df[k])) for k in ("Open", "High", "Low", "Close"))
        src = "yfinance_daily"
    ema20 = _ema(c)
    sma50 = [sum(c[max(0, i - 49):i + 1]) / min(i + 1, 50) for i in range(len(c))]
    atr14 = _atr(h, l, c)
    return SimpleNamespace(dates=dates, close=c, high=h, low=l,
                           ema20=ema20, sma50=sma50, atr14=atr14, source=src)


def _intraday_15m(sym):
    """Current-session 15m bars: OUR 5m store first (mostly ibkr_web-sourced,
    uploaded to S3 on every partition write, so the Lambda reads them
    minutes-fresh), resampled 3x5m -> 15m on completed windows only.
    yfinance stays as the labelled fallback — owner, 6 Sep: "we do have it
    from ibkr as well". IBKR is the golden source; the fallback is visible.
    """
    bars = _store_15m(sym)
    if bars:
        return bars
    from ..yahoo_session import yahoo_session
    import yfinance as yf
    df = yf.Ticker(sym, session=yahoo_session()).history(period="2d", interval="15m", prepost=True)
    if df is None or df.empty:
        return []
    today_et = _dt.datetime.now(ET).date()
    out = []
    for ts, row in df.iterrows():
        if ts.astimezone(ET).date() == today_et:
            out.append({"t": ts.isoformat(), "o": float(row["Open"]),
                        "h": float(row["High"]), "l": float(row["Low"]),
                        "c": float(row["Close"]), "src": "yfinance_15m"})
    return out


def _store_15m(sym, day_et: "_dt.date | None" = None):
    """Today's completed 15m windows from the 5m store partition, or []."""
    try:
        from ..bar_cache import asset_classes as _p  # noqa: F401 — registers
        from ..bar_cache.store import BarStore
        from ..ibkr_bars import route_asset_class
        from pathlib import Path as _P
        store = BarStore(base_dir=_P("~/.tradepro/bar_cache").expanduser())
        now = _dt.datetime.now(_dt.UTC)
        res = store.get(sym, route_asset_class(sym), "5m",
                        now - _dt.timedelta(days=4), now,
                        allow_partial=True, skip_fetch=True,
                        fetched_by="preearnings_watch")
        df = getattr(res, "df", res)
        if df is None or df.empty:
            return []
        day = day_et or _dt.datetime.now(ET).date()
        out, win = [], {}
        for ts, row in df.iterrows():
            ts_et = ts.astimezone(ET)
            if ts_et.date() != day:
                continue
            key = ts_et.replace(minute=(ts_et.minute // 15) * 15,
                                second=0, microsecond=0)
            w = win.setdefault(key, {"t": key.isoformat(),
                                     "o": float(row["open"]), "h": float(row["high"]),
                                     "l": float(row["low"]), "c": float(row["close"]),
                                     "n": 0, "src": "store_5m/" + str(row.get("source", "?"))})
            w["h"] = max(w["h"], float(row["high"]))
            w["l"] = min(w["l"], float(row["low"]))
            w["c"] = float(row["close"])
            w["n"] += 1
        # COMPLETED windows only — the spec confirms on completed bars, and a
        # window is complete when its 3 five-minute bars exist or its end has
        # passed (a halt can leave a short window that is still closed).
        cutoff = _dt.datetime.now(ET)
        return [w for k, w in sorted(win.items())
                if w["n"] >= 3 or (k + _dt.timedelta(minutes=15)) <= cutoff]
    except Exception:  # noqa: BLE001 — the fallback chain handles it
        return []


def _confirmed_print(base, token, sym):
    """Exactly ONE future print or the module blocks (the 21st/30th lesson)."""
    import requests
    r = requests.get(f"{base}/api/earnings-calendar/{sym}",
                     params={"back": 5, "ahead": 200},
                     headers={"Authorization": f"Bearer {token}"} if token else {},
                     timeout=30)
    today = _dt.date.today().isoformat()
    fut = sorted({(str(e.get("report_date"))[:10], str(e.get("session") or "?"))
                  for e in (r.json().get("events") or [])
                  if str(e.get("report_date"))[:10] >= today})
    return fut


def _sessions_between(d0: _dt.date, d1: _dt.date) -> int:
    n, d = 0, d0
    while d < d1:
        d += _dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _options_context(base, token, sym, print_date):
    """§13/§7: how the market is PLACING it — context, never direction.

    Reads the chain the capture lane already stores daily (option_quote_daily,
    mostly ibkr_web-sourced). Computes the numbers the spec's valid-uses list
    names: ATM IV on a pre-print expiry vs one CROSSING the print (the event
    premium), the crossing straddle's implied move, put/call OI ratio and the
    top OI strikes near spot. The forbidden-conclusions list applies: these
    are displayed, they never create a directional action.
    """
    import requests
    try:
        r = requests.get(f"{base}/api/options/quotes-daily/{sym}",
                         headers={"Authorization": f"Bearer {token}"} if token else {},
                         params={"days": 4}, timeout=30)
        body = r.json() if r.status_code == 200 else {}
        legs = body if isinstance(body, list) else (body.get("quotes") or [])
        if not legs:
            return {"status": "INSUFFICIENT", "reason": "no captured chain"}
        cap = max(str(x.get("capture_date"))[:10] for x in legs)
        legs = [x for x in legs if str(x.get("capture_date"))[:10] == cap]
        spot = next((float(x["spot"]) for x in legs if x.get("spot")), None)
        if not spot:
            return {"status": "INSUFFICIENT", "reason": "no spot on captured legs"}
        exps = sorted({str(x.get("expiry"))[:10] for x in legs})
        pre = [e for e in exps if e < print_date]
        cross = [e for e in exps if e >= print_date]

        def atm_iv(exp):
            c = [x for x in legs if str(x.get("expiry"))[:10] == exp and x.get("iv")]
            if not c:
                return None
            return round(float(min(c, key=lambda x: abs(float(x["strike"]) - spot))["iv"]), 3)

        def straddle_move(exp):
            near = [x for x in legs if str(x.get("expiry"))[:10] == exp
                    and x.get("bid") and x.get("ask")]
            if not near:
                return None
            k = min({float(x["strike"]) for x in near}, key=lambda v: abs(v - spot))
            mids = {}
            for x in near:
                if abs(float(x["strike"]) - k) < 1e-6:
                    mids[x["right"]] = (float(x["bid"]) + float(x["ask"])) / 2
            if "C" in mids and "P" in mids:
                return (mids["C"] + mids["P"]) / spot
            return None

        oi_p = sum(int(x.get("open_interest") or 0) for x in legs if x.get("right") == "P")
        oi_c = sum(int(x.get("open_interest") or 0) for x in legs if x.get("right") == "C")
        near = sorted((x for x in legs if x.get("open_interest")
                       and abs(float(x["strike"]) / spot - 1) < 0.10),
                      key=lambda x: -int(x["open_interest"]))[:3]
        iv_pre = atm_iv(pre[-1]) if pre else None
        iv_cross = atm_iv(cross[0]) if cross else None
        # FULL TERM STRUCTURE — owner, 6 Sep: "we shd be looking at options at
        # diff expiry and not just 30 sep". Every captured expiry, ATM IV and
        # straddle-implied move, so the event premium is visible as the KINK
        # in the curve rather than one pairwise difference.
        term = []
        for e in exps:
            mv = straddle_move(e)
            term.append({"expiry": e, "crosses_print": e >= print_date,
                         "atm_iv": atm_iv(e),
                         "implied_move_pct": (round(100 * mv, 2) if mv else None)})
        return {
            "term_structure": term,
            "status": "CONTEXT_AVAILABLE", "capture_date": cap, "spot": spot,
            "atm_iv_pre": iv_pre, "pre_expiry": (pre[-1] if pre else None),
            "atm_iv_cross": iv_cross, "cross_expiry": (cross[0] if cross else None),
            "event_iv_premium": (round(iv_cross - iv_pre, 4)
                                 if iv_pre and iv_cross else None),
            "implied_move_cross_pct": (lambda m: round(100 * m, 2) if m else None)(
                straddle_move(cross[0]) if cross else None),
            "put_call_oi_ratio": round(oi_p / oi_c, 2) if oi_c else None,
            "top_oi": [{"strike": float(x["strike"]), "right": x["right"],
                        "oi": int(x["open_interest"])} for x in near],
        }
    except Exception as exc:  # noqa: BLE001 — context, never a blocker
        return {"status": "INSUFFICIENT", "reason": str(exc)[:80]}


# ── the evaluation ────────────────────────────────────────────────────────

def evaluate(sym, cfg, base, token, state):
    """One cycle. Returns (primary_action, detail, alerts, candidate_row)."""
    alerts = []      # (alert_id, state_detail, human_text)
    gates = []

    def gate(name, ok, measured, threshold=""):
        # The desk's Detail renderer contract: {gate, actual, threshold,
        # verdict}. The first cut emitted {ok, measured} and every cell
        # rendered as a dash — a shape mismatch is indistinguishable from
        # missing data on screen, which is exactly what the owner saw.
        gates.append({"gate": name, "actual": str(measured)[:110],
                      "threshold": threshold,
                      "verdict": "pass" if ok else "FAIL"})
        return ok

    # -- calendar: exactly one confirmed future print --
    prints = _confirmed_print(base, token, sym)
    if len(prints) > 1:
        # A CONFLICT blocks everything — two future dates is the MU 21st/30th
        # hazard and no source gets picked by guesswork (addendum §6.2).
        detail = f"{len(prints)} conflicting future print(s): {prints[:3]}"
        gate("one_confirmed_print", False, detail)
        return ("REVIEW_REQUIRED", f"earnings date CONFLICT — {detail}",
                alerts, None), gates
    if not prints:
        # UNKNOWN is different from CONFLICT (addendum §6.2 table): the
        # engine still WATCHES and alerts — pullback, reclaim, breakout,
        # do-not-chase — but no swing proposal can exist, because
        # SWING_DATA_OK requires a VERIFIED date. SNDK starts here.
        gate("one_confirmed_print", False, "no confirmed future print — "
             "earnings UNKNOWN; alerts on, swing proposals blocked")
        pdate, sessions_to, e_state = None, None, "UNKNOWN"
    else:
        pdate = _dt.date.fromisoformat(prints[0][0])
        gate("one_confirmed_print", True, f"{prints[0][0]} {prints[0][1]}")
        sessions_to = _sessions_between(_dt.date.today(), pdate)
        e_state = ("POST_EVENT" if sessions_to < 0 else
                   "EVENT_DAY" if sessions_to == 0 else
                   "CAUTION" if sessions_to <= 2 else "NORMAL")
    for k, s_id in (((5, "EARNINGS_5D"), (2, "EARNINGS_2D"), (0, "EARNINGS_DAY"))
                    if sessions_to is not None else ()):
        if sessions_to == k:
            alerts.append((s_id, prints[0][0],
                           f"{sym}: {k} trading session(s) to the print"
                           + (" — NO new swing entries; exit any swing before "
                              "the close" if k == 0 else "")))

    if e_state == "POST_EVENT":
        return ("CYCLE_COMPLETE", "print has passed — alerts expired; renewal "
                "needs refreshed levels, ATR, date and owner approval",
                alerts, None), gates

    opts = (_options_context(base, token, sym, prints[0][0]) if prints
            else {"status": "INSUFFICIENT", "reason": "no confirmed print to "
                                                       "anchor expiries"})
    gate("options_context", opts.get("status") == "CONTEXT_AVAILABLE",
         (f"IV pre {opts.get('atm_iv_pre')} vs cross {opts.get('atm_iv_cross')} "
          f"(+{opts.get('event_iv_premium')}) · implied move "
          f"{opts.get('implied_move_cross_pct')}% · P/C OI "
          f"{opts.get('put_call_oi_ratio')}"
          if opts.get("status") == "CONTEXT_AVAILABLE"
          else opts.get("reason", "INSUFFICIENT")))

    # -- daily context (settled store) --
    d = _daily(sym)
    i = len(d.close) - 1
    px, ema, sma, atr = d.close[i], d.ema20[i], d.sma50[i], d.atr14[i]
    # Three regime states (advisor clarification, 6 Sep): a binary
    # "SMA50 non-falling" wrongly vetoes current strength when an old spike
    # rolls out of the 50-day window — exactly MU's situation. TOLERATED
    # allows alerts and manual review WITH the warning; only QUALIFIED
    # allows a standard proposal path.
    sma_fall_pct = (d.sma50[i] / d.sma50[i - 5] - 1) * 100
    tol = float(cfg.get("sma50_rollover_tolerance_pct", 2.0))
    above = px > ema and px > sma
    ema_ok = d.ema20[i] >= d.ema20[i - 3]
    if above and ema_ok and d.sma50[i] >= d.sma50[i - 5]:
        regime = "QUALIFIED"
    elif above and ema_ok and sma_fall_pct >= -tol:
        regime = "TOLERATED_SMA50_ROLLOVER"
    else:
        regime = "BLOCKED"
    long_regime = regime != "BLOCKED"
    gate("regime", long_regime,
         f"{regime}: close {px:.2f} vs EMA20 {ema:.2f} / SMA50 {sma:.2f}, "
         f"SMA50 5s slope {sma_fall_pct:+.2f}% (tolerance −{tol}%)")

    # -- sector (daily-level; intraday proxy return via its last two closes) --
    try:
        p = _daily(cfg["sector_proxy"])
        proxy_ret = (p.close[-1] / p.close[-2] - 1) * 100
        sector_ok = gate("sector_ok",
                         proxy_ret >= cfg["sector_floor_pct"],
                         f"{cfg['sector_proxy']} {proxy_ret:+.2f}% "
                         f"(floor {cfg['sector_floor_pct']}%)")
    except SystemExit:
        sector_ok = False
        gate("sector_ok", False, f"{cfg['sector_proxy']} bars unavailable")

    # -- intraday: touches and reclaims on completed 15m bars --
    bars = _intraday_15m(sym)
    band = cfg["dynamic_bands"]
    prox_hi = ema + band["ema_proximity_atr"] * atr
    touched = any(b["l"] <= prox_hi for b in bars)
    touch_low = min((b["l"] for b in bars if b["l"] <= prox_hi), default=None)
    reclaim_bar = None
    if touched:
        seen_touch = False
        for b in bars:
            if b["l"] <= prox_hi:
                seen_touch = True
            elif seen_touch and b["c"] > prox_hi:
                reclaim_bar = b
                break
    if bars and bars[0]["o"] < min(ema - band["gap_down_atr"] * atr,
                                   d.close[i - 1] - band["gap_down_atr"] * atr):
        alerts.append(("GAP_DOWN", bars[0]["t"][:10],
                       f"{sym} opened {bars[0]['o']:.2f}, more than "
                       f"{band['gap_down_atr']}x ATR below reference — "
                       f"automatic entries blocked, wait 30m, new reclaim required"))
        return ("REVIEW_REQUIRED", "gap-down protection", alerts, None), gates
    # -- breakout watch (M1 momentum context) --------------------------------
    # Owner, 6 Sep: "the opportunity of MU breaching 1050 tomorrow... it can
    # go either way." Exactly — so the level is ARMED, not predicted: the
    # first completed 15m close through it fires one alert, and the alert
    # carries the honest regime state, because under the spec's own
    # sma50_non_falling rule M1 cannot QUALIFY until the advisor answers the
    # tolerance question. Alerting and qualifying are different claims.
    bw = cfg.get("breakout_watch_level")
    if not bw and cfg.get("breakout_watch_mode") == "20d_high":
        bw = round(max(d.high[-20:]), 2)   # recomputed live, never stored
    # SNDK special filter (addendum §12): do not chase a large expansion day.
    ext = ema + band.get("extended_from_ema_atr", 1.5) * atr
    if px >= ext or (bars and bars[-1]["c"] >= ext):
        alerts.append(("EXTENDED_DO_NOT_CHASE", d.dates[i],
                       f"{sym} is ≥{band.get('extended_from_ema_atr', 1.5)}x "
                       f"ATR above its EMA20 ({ext:.2f}) — extension, not "
                       f"entry. Do not chase; wait for a pullback/reclaim."))
    if bw:
        bo = next((b for b in bars if b["c"] >= bw), None)
        if bo:
            alerts.append((
                "BREAKOUT_15M", f"{bw:.0f}",
                f"{sym} 15m close {bo['c']:.2f} ≥ {bw:.0f} breakout watch "
                f"(M1). EMA20 {ema:.2f} · SMA50 {sma:.2f} · ATR14 {atr:.2f} · "
                f"sector {'OK' if sector_ok else 'WEAK'} · earnings "
                f"{e_state} ({sessions_to}s) · regime {regime} · options "
                + (f"IV cross {opts.get('atm_iv_cross')} (event prem "
                   f"+{opts.get('event_iv_premium')}), implied move "
                   f"{opts.get('implied_move_cross_pct')}%, P/C OI "
                   f"{opts.get('put_call_oi_ratio')}"
                   if opts.get("status") == "CONTEXT_AVAILABLE"
                   else "INSUFFICIENT")
                + f" · daily src {d.source}. One "
                f"alert, no forecast, no order — manual decision."))

    if touched:
        alerts.append(("EMA20_PULLBACK_ZONE", d.dates[i],
                       f"{sym} touched the EMA20 proximity band "
                       f"({prox_hi:.2f}; low {touch_low:.2f}) · "
                       f"blind-entry proxy {prox_hi:.2f} · journal armed"))
    if reclaim_bar:
        alerts.append(("RECLAIM_15M", reclaim_bar["t"],
                       f"{sym} 15m close {reclaim_bar['c']:.2f} back above the "
                       f"band after the touch (touch low {touch_low:.2f}, "
                       f"blind proxy {prox_hi:.2f}) — forward journal row "
                       f"written; MAE/MFE fill in over the session"))

    # -- primary action + proposal --
    def trigger(text):
        gates.append({"gate": "→ what changes this", "actual": text[:120],
                      "threshold": "", "verdict": "armed"})

    if not long_regime:
        why = (f"DECISION: no long. Close {px:.2f} vs EMA20 {ema:.2f} / SMA50 "
               f"{sma:.2f} — structure is below the trend filter"
               + (f" after a pullback (ATR {100*atr/px:.1f}% of price)" if px < sma else "")
               + ". Nothing to do until price repairs.")
        trigger(f"daily close back above SMA50 ({sma:.2f}) re-opens setups")
        trigger(f"a 15m reclaim of the EMA20 band ({prox_hi:.2f}) after a "
                f"touch would surface for manual review only")
        return ("BLOCK_NEW_ENTRIES", why, alerts,
                _row(sym, cfg, "block", px, sma, None, sessions_to, why,
                     provenance=_provenance(d, bars, opts),
                     level_label="sma50", options=opts)), gates
    if not (touched and reclaim_bar):
        oc = (f" Options: market prices the print at ±{opts['implied_move_cross_pct']}%"
              if opts.get("implied_move_cross_pct") else
              (f" Options: +{round(100*opts['event_iv_premium'],1)} IV pts of event "
               f"premium, P/C OI {opts.get('put_call_oi_ratio')}"
               if opts.get("event_iv_premium") else ""))
        why = (f"DECISION: stand aside, watching ({regime}). No entry exists until "
               f"a pullback to the EMA20 band ({prox_hi:.2f}) is RECLAIMED on a "
               f"15m close"
               + (f", or a 15m close ≥{bw:.0f} flags M1 momentum for manual review"
                  if bw else "")
               + (f". {sessions_to} sessions to the confirmed print — swing exits "
                  f"before it." if sessions_to is not None else
                  ". No confirmed earnings date — proposals stay blocked.")
               + oc)
        trigger(f"touch of {prox_hi:.2f} then a 15m close back above it → "
                f"setup review" + ("" if regime == "QUALIFIED" else
                                   f" (regime {regime}: review only, no proposal)"))
        if bw:
            trigger(f"15m close ≥ {bw:.0f} → M1 momentum alert (one-shot)")
        trigger(f"open below ~{(ema - band['gap_down_atr']*atr):.0f} → gap-down "
                f"guard blocks the day, fresh reclaim required")
        return ("WATCH", why, alerts,
                _row(sym, cfg, "watch", px, prox_hi, None, sessions_to, why,
                     provenance=_provenance(d, bars, opts),
                     level_label="band", options=opts)), gates
    if regime == "TOLERATED_SMA50_ROLLOVER":
        msg = (f"reclaim seen under TOLERATED regime (SMA50 rolling over "
               f"{sma_fall_pct:+.2f}%/5s) — manual review with the warning, "
               f"no automatic proposal")
        alerts.append(("RECLAIM_TOLERATED_REGIME", d.dates[i], f"{sym}: {msg}"))
        return ("REVIEW_REQUIRED", msg, alerts,
                _row(sym, cfg, "review", reclaim_bar["c"], None, None,
                     sessions_to, msg)), gates
    if not sector_ok:
        return ("REVIEW_REQUIRED", "setup reclaimed but sector filter failed",
                alerts, _row(sym, cfg, "review", None, None, None, sessions_to,
                             "reclaim seen, sector weak — manual review")), gates

    # SETUP_QUALIFIED → size it, or say exactly why not
    entry = reclaim_bar["c"]
    ar = cfg["atr_risk"]
    atr_stop = entry - ar["default_stop_distance_atr"] * atr
    struct_stop = ((touch_low - ar["structural_buffer_atr"] * atr)
                   if touch_low is not None else atr_stop)
    stop = min(atr_stop, struct_stop)
    dist = entry - stop
    if dist > ar["maximum_unapproved_stop_distance_atr"] * atr:
        msg = (f"required structural stop is {dist/atr:.2f}x ATR — beyond the "
               f"{ar['maximum_unapproved_stop_distance_atr']}x cap. Reduce "
               f"size, skip, or explicitly approve wider risk.")
        alerts.append(("RISK_OVERFLOW", d.dates[i], f"{sym}: {msg}"))
        return ("REVIEW_REQUIRED", msg, alerts,
                _row(sym, cfg, "review", entry, stop, None, sessions_to, msg)), gates

    budget = cfg.get("max_risk_per_swing_trade_currency")
    if not budget:
        msg = ("CONFIGURATION_BLOCKED: set max_risk_per_swing_trade_currency "
               "(and max_gap_risk_for_core_currency) in settings-kv "
               f"preearnings_cfg_{sym} — a share clip may not substitute for "
               "a risk budget")
        alerts.append(("CONFIGURATION_BLOCKED", "risk_budget", f"{sym}: {msg}"))
        return ("SETUP_QUALIFIED", msg, alerts,
                _row(sym, cfg, "qualified", entry, stop, None, sessions_to, msg)), gates

    qty = min(int(budget / dist), int(cfg["max_swing_shares"]))
    if qty < 1:
        return ("NO_TRADE", "risk budget buys less than one share at this "
                "stop distance", alerts,
                _row(sym, cfg, "no-trade", entry, stop, None, sessions_to,
                     "budget < 1 share at selected stop")), gates
    why = (f"ORDER_PROPOSAL (NOT sent): BUY {qty} @ ~{entry:.2f} LMT, stop "
           f"{stop:.2f} ({dist/atr:.2f}x ATR, "
           f"{'structure' if struct_stop < atr_stop else 'ATR'}-selected), "
           f"exit all swing before the {pdate} print")
    alerts.append(("ORDER_PROPOSAL", reclaim_bar["t"], f"{sym}: {why}"))
    return ("ORDER_PROPOSAL", why, alerts,
            _row(sym, cfg, "buy (proposal)", entry, stop, qty, sessions_to, why)), gates


def _provenance(d, bars, opts):
    """The Data panel's contract: {label, source_label, trust, age}."""
    rows = [{"label": "daily bars", "source_label": d.source,
             "trust": ("golden" if d.source == "bar_store" else "fallback"),
             "age": d.dates[-1]}]
    if bars:
        rows.append({"label": "intraday 15m", "source_label": bars[-1].get("src", "?"),
                     "trust": ("golden" if str(bars[-1].get("src", "")).startswith("store_5m")
                               else "fallback"),
                     "age": str(bars[-1]["t"])[:16]})
    else:
        rows.append({"label": "intraday 15m", "source_label": "no session bars yet",
                     "trust": "unavailable", "age": ""})
    if opts.get("status") == "CONTEXT_AVAILABLE":
        rows.append({"label": "options chain", "source_label": "capture lane (ibkr)",
                     "trust": "vendor", "age": opts.get("capture_date", "")})
    else:
        rows.append({"label": "options chain",
                     "source_label": opts.get("reason", "INSUFFICIENT"),
                     "trust": "unavailable", "age": ""})
    return rows


def _row(sym, cfg, action, entry, stop, qty, sessions_to, why,
         gates_extra=None, provenance=None, level_label="stop", options=None):
    """Owner, 6 Sep, looking at the desk: the Pre-Earn label "is fne for MU
    but not for SNDK as SNDK has no recent earning coming up". Right — the
    lane is named by what the symbol is actually IN: an earnings cycle
    (confirmed print inside 25 sessions) or plain swing watching."""
    from ..candidates import Candidate, emit
    lane = ("Pre-Earn" if sessions_to is not None and sessions_to <= 25
            else "SwingWatch")
    return emit([Candidate(
        symbol=sym, strategy=lane, tier="unproven", action=action,
        as_of=_dt.datetime.now(_dt.UTC).isoformat(),
        entry=entry, level=stop, level_label=level_label,
        metric=(float(sessions_to) if sessions_to is not None else None),
        metric_label="d→ER",
        eligible=True, why=why[:320],
        provenance=provenance or [],
        extra={"strategy_version": STRATEGY_VERSION,
               "proposed_qty": qty,
               "options_context": options},
    )])[0]


# ── main ──────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-preearnings-watch")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from .push_to_api import load_credentials
    base, token = load_credentials()
    base = base.rstrip("/")

    symbols = _kv_get(base, token, "preearnings_symbols")
    if not symbols:
        symbols = ["MU"]
        if not args.dry_run:
            _kv_put(base, token, "preearnings_symbols", symbols,
                    "Pre-earnings watch: active symbols",
                    "Symbols the pre-earnings Phase 1 engine evaluates. Each "
                    "needs a preearnings_cfg_<SYM> key. Cycle expires at the "
                    "confirmed print; renewal is a decision.", create=True)

    rows, journal_add, mail_lines = [], [], []
    for sym in symbols:
        cfg = _kv_get(base, token, f"preearnings_cfg_{sym}")
        if not cfg:
            if sym == "MU":
                cfg = MU_DEFAULT_CFG
                if not args.dry_run:
                    _kv_put(base, token, f"preearnings_cfg_{sym}", cfg,
                            f"Pre-earnings config: {sym}",
                            "v1.1 parameters. reference_zones are CHART "
                            "REFERENCE; triggers are dynamic (EMA/ATR). "
                            "max_risk_per_swing_trade_currency and "
                            "max_gap_risk_for_core_currency MUST be set by "
                            "the owner — the engine is CONFIGURATION_BLOCKED "
                            "for sizing until both exist.", create=True)
            else:
                log.warning("%s: no preearnings_cfg_%s — skipped, config needed",
                            sym, sym)
                continue

        state = _kv_get(base, token, f"preearnings_state_{sym}") or {"fired": {}}
        (action, detail, alerts, row), gates = evaluate(sym, cfg, base, token, state)
        log.info("%s → %s: %s", sym, action, detail[:140])
        if row:
            row["gates"] = gates
            rows.append(row)

        fresh = []
        for a_id, s_detail, text in alerts:
            key = f"{a_id}|{s_detail}"
            if key not in state["fired"]:
                state["fired"][key] = _dt.datetime.now(_dt.UTC).isoformat()
                fresh.append((a_id, text))
                journal_add.append({"symbol": sym, "alert": a_id,
                                    "detail": s_detail, "text": text,
                                    "at": state["fired"][key],
                                    "action": action})
        if fresh and not args.dry_run:
            mail_lines += [f"  {a_id:24} {text}" for a_id, text in fresh]
        elif fresh:
            for a_id, text in fresh:
                print(f"  WOULD ALERT {a_id}: {text}")
        if not args.dry_run:
            state["last_eval"] = {"at": _dt.datetime.now(_dt.UTC).isoformat(),
                                  "action": action, "detail": detail[:200]}
            _kv_put(base, token, f"preearnings_state_{sym}", state,
                    f"Pre-earnings state: {sym}",
                    "Engine state + fired-alert dedupe keys + journal. "
                    "Cleared only on deliberate cycle renewal.", create=True)

    # -- publish to the board (screen + regular digest email ride this) --
    if rows and not args.dry_run:
        import requests
        art = {"as_of_utc": _dt.datetime.now(_dt.UTC).isoformat(),
               "strategy_version": STRATEGY_VERSION,
               "candidates_v2": rows, "journal_delta": journal_add}
        r = requests.post(f"{base}/api/ingest/today-setups",
                          json={"universe": "preearnings", "label": "latest",
                                "uploaded_by": "preearnings-watch", "artifact": art},
                          headers={"Authorization": f"Bearer {token}"} if token else {},
                          timeout=45)
        log.info("board push → HTTP %s (%d row(s))", r.status_code, len(rows))

    # -- immediate transition mail (single channel, deduped upstream) --
    if mail_lines:
        try:
            from .email_digest import CRED_PATH, send_email
            cfg_mail = json.loads(CRED_PATH.read_text())
            subject = f"[PRE-EARN] {len(mail_lines)} alert(s) — " + \
                      _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d %H:%M")
            body = ("Pre-earnings watch — state transitions only, each fires "
                    "once.\n\n" + "\n".join(mail_lines)
                    + "\n\nNothing here is placed automatically. Board → "
                      "Candidates → Pre-Earn.")
            send_email(SimpleNamespace(subject=subject, text_body=body,
                                       html_body=None, pdf_bytes=None), cfg_mail)
            log.info("alert mail sent: %s", subject)
        except Exception as exc:  # noqa: BLE001 — the board still has the state
            log.warning("alert mail NOT sent: %s", str(exc)[:160])

    try:
        from ..run_log import log_run
        log_run("preearnings-watch", "watch", "ok",
                summary=f"{len(symbols)} symbol(s), {len(mail_lines)} alert(s)")
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
