"""tradepro-algo-digest — daily systematic-trading digest.

End-of-day (or pre-open) email summarising the trader's algo state:

  • System state — frozen / panic warning if non-normal.
  • Today's target portfolio — what the algo wants to hold tomorrow.
  • Today's trade plan — diff vs current broker positions (action list).
  • Risk events — what got blocked + why.
  • Position drift — what the broker disagrees with us about.
  • Cost honesty — backtest assumption vs realised live cost.
  • Validation summary — latest equity-pipeline backtest stats.

Pulls everything from the existing API endpoints we built in steps
1-6. Composes a single text/HTML email + sends via the same
send_email machinery the long-term digest uses. Run from launchd
(plist install in scripts/install-launchd.sh) post-close UTC.

Usage:
    tradepro-algo-digest --strategy ichimoku_equity --send
    tradepro-algo-digest --strategy ichimoku_equity --print  (dry-run)

The digest is purpose-built — it does NOT extend the long-term
email_digest.py, which serves a different audience (Decide/Compare).
Cleaner to have a sibling than to fork the existing template.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

import requests

from ..email_digest import EmailDigest
from ..secrets import get_secret

log = logging.getLogger("tradepro.cli.algo_digest")


# ---------------------------------------------------------------------- #
# API readers — every section reads one of our endpoints from steps 1-6
# ---------------------------------------------------------------------- #


def _api_get(base: str, path: str, token: str) -> Any | None:
    """GET helper. Returns None on any failure (silent fallthrough so
    one dead endpoint doesn't kill the whole digest)."""
    try:
        resp = requests.get(
            f"{base.rstrip('/')}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        if resp.status_code == 404:
            return None
        if not 200 <= resp.status_code < 300:
            log.warning("%s HTTP %d: %s", path, resp.status_code, resp.text[:120])
            return None
        return resp.json()
    except requests.RequestException as exc:
        log.warning("%s fetch failed: %s", path, exc)
        return None


def fetch_state(base: str, token: str, strategy: str) -> dict[str, Any]:
    return {
        "system_state":   _api_get(base, "/api/system/state", token),
        "live_portfolio": _api_get(base, f"/api/live-portfolio/{strategy}/latest", token),
        "trade_plan":     _api_get(base, f"/api/trade-plan/{strategy}", token),
        "risk_summary":   _api_get(base, "/api/risk/summary", token),
        "risk_events":    _api_get(base, "/api/risk/events?limit=20", token),
        "drift":          _api_get(base, "/api/positions/drift?unresolved=true&limit=20", token),
        "cost_feedback":  _api_get(base, f"/api/cost-feedback/{strategy}", token),
        "validation":     _api_get(base, f"/api/equity-pipeline/{strategy}/latest", token),
    }


# ---------------------------------------------------------------------- #
# Section builders — each returns (text_block, html_block)
# ---------------------------------------------------------------------- #


def _hdr(text: str) -> str:
    line = "═" * 68
    return f"\n{line}\n  {text.upper()}\n{line}\n"


def _section_system_state(state: dict | None) -> tuple[str, str]:
    if not state:
        return "", ""
    mode = state.get("mode", "normal")
    if mode == "normal":
        return "", ""
    reason = state.get("reason") or "(no reason)"
    set_by = state.get("setBy") or "?"
    set_at = state.get("setAtUtc") or "?"
    label = "🛑 PANIC" if mode == "panic" else "⏸  FROZEN"
    text = (
        f"\n[!] SYSTEM IS {label}\n"
        f"    reason : {reason}\n"
        f"    set by : {set_by}\n"
        f"    when   : {set_at}\n"
        f"    action : resume via POST /api/system/resume\n"
    )
    color = "#ef4444" if mode == "panic" else "#f59e0b"
    bg = "rgba(239,68,68,0.10)" if mode == "panic" else "rgba(245,158,11,0.10)"
    html = (
        f'<div style="padding:14px;background:{bg};border:1px solid {color};'
        f'border-radius:8px;margin-bottom:18px">'
        f'<strong style="color:{color};font-size:14px">{label}</strong> '
        f'<span style="color:#444">{reason}</span><br>'
        f'<small style="color:#666">set by {set_by} at {set_at} · POST '
        f'/api/system/resume to restore</small></div>'
    )
    return text, html


def _section_live_portfolio(env: dict | None) -> tuple[str, str]:
    if not env:
        return _hdr("Today's target portfolio") + "  (no slow-loop run yet)\n", ""
    summary = env.get("summary") or {}
    decisions = env.get("decisions") or []
    longs = [d for d in decisions if (d.get("targetWeight") or 0) > 0]
    regime = env.get("regimeState") or "?"
    vol_scalar = summary.get("vol_scalar") or 1.0
    sleeves = summary.get("sleeves") or []

    lines = [
        f"  as of     : {env.get('asOfUtc')}",
        f"  regime    : {regime}   vol_scalar {vol_scalar:.2f}",
        f"  longs     : {len(longs)} positions across {len(sleeves)} sleeve(s)",
        "",
    ]
    for s in sleeves:
        lines.append(
            f"    sleeve {s['name']:14} n_long {s['n_long']:3} / {s['n_tickers']:3}"
            f"   weight {s['ensemble_weight'] * 100:5.1f}%"
        )
    longs.sort(key=lambda d: -(d.get("targetWeight") or 0))
    if longs:
        lines += ["", "  top 10 by weight:"]
        for d in longs[:10]:
            lines.append(
                f"    {d['symbol']:8}  target {d['targetWeight'] * 100:5.2f}%"
                f"   {d['sleeve']}"
            )
    text = _hdr("Today's target portfolio") + "\n".join(lines) + "\n"

    rows_html = "".join(
        f'<tr><td>{d["symbol"]}</td>'
        f'<td style="text-align:right;font-family:monospace">'
        f'{d["targetWeight"] * 100:.2f}%</td>'
        f'<td><small>{d["sleeve"]}</small></td></tr>'
        for d in longs[:15]
    )
    html = (
        f'<h3>Today\'s target portfolio</h3>'
        f'<p><strong>Regime:</strong> {regime} · '
        f'<strong>Vol scalar:</strong> {vol_scalar:.2f} · '
        f'<strong>{len(longs)}</strong> long positions</p>'
        f'<table cellpadding="4" style="border-collapse:collapse;font-size:13px">'
        f'<thead><tr><th align="left">Symbol</th><th align="right">Target</th>'
        f'<th align="left">Sleeve</th></tr></thead>'
        f'<tbody>{rows_html}</tbody></table>'
    )
    return text, html


def _section_trade_plan(plan: dict | None) -> tuple[str, str]:
    if not plan or not plan.get("hasPlan"):
        reason = plan.get("noPlanReason") if plan else "no plan endpoint response"
        return _hdr("Today's trade plan") + f"  (no plan: {reason})\n", ""

    summary = plan.get("summary") or {}
    intents = plan.get("intents") or []
    lines = [
        f"  buys     : {summary.get('nBuys', 0)}"
        f"   sells {summary.get('nSells', 0)}"
        f"   skipped {summary.get('nSkipped', 0)}",
        f"  gross    : ${summary.get('grossFlow', 0):,.0f}"
        f"  ({summary.get('grossFlowPct', 0):.1f}% of portfolio)",
        f"  net flow : ${summary.get('netFlow', 0):,.0f}",
        "",
    ]
    intents_sorted = sorted(intents, key=lambda i: -abs(i.get("diffNotional") or 0))
    for i in intents_sorted[:15]:
        diff = i.get("diffNotional") or 0
        risk = i.get("riskClass") or "—"
        lines.append(
            f"    {i['side']:4} {i['symbol']:8} "
            f"qty {float(i.get('qty') or 0):>8.2f} "
            f"diff ${diff:>10,.0f}  risk {risk:8}  {i.get('reason', '')[:40]}"
        )
    if len(intents_sorted) > 15:
        lines.append(f"    ... + {len(intents_sorted) - 15} more")
    text = _hdr("Today's trade plan") + "\n".join(lines) + "\n"

    rows_html = "".join(
        f'<tr>'
        f'<td style="color:{"#1fc16b" if i["side"] == "BUY" else "#ef4444"}">{i["side"]}</td>'
        f'<td><strong>{i["symbol"]}</strong></td>'
        f'<td style="text-align:right;font-family:monospace">'
        f'{float(i.get("qty") or 0):.2f}</td>'
        f'<td style="text-align:right;font-family:monospace">'
        f'${i.get("diffNotional") or 0:,.0f}</td>'
        f'<td><small>{i.get("reason", "")[:50]}</small></td>'
        f'</tr>'
        for i in intents_sorted[:15]
    )
    html = (
        f'<h3>Today\'s trade plan</h3>'
        f'<p><strong>{summary.get("nBuys", 0)} BUY</strong> · '
        f'<strong>{summary.get("nSells", 0)} SELL</strong> · '
        f'gross ${summary.get("grossFlow", 0):,.0f} '
        f'({summary.get("grossFlowPct", 0):.1f}% of portfolio)</p>'
        f'<table cellpadding="4" style="border-collapse:collapse;font-size:13px">'
        f'<tbody>{rows_html}</tbody></table>'
    )
    return text, html


def _section_risk(summary: dict | None, events: dict | None) -> tuple[str, str]:
    if not summary:
        return "", ""
    by_decision = summary.get("byDecision") or {}
    if not by_decision:
        return "", ""
    blocked = by_decision.get("BLOCKED", 0)
    allowed = by_decision.get("ALLOWED", 0)
    if blocked == 0 and allowed == 0:
        return "", ""
    by_gate = summary.get("blockedByGate") or []
    lines = [
        f"  allowed   : {allowed}",
        f"  blocked   : {blocked}",
    ]
    if by_gate:
        lines.append("  blocks by gate:")
        for g in by_gate:
            lines.append(f"    {g['gate']:24} {g['count']}")
    if events and (evs := events.get("events") or []):
        recent_blocked = [e for e in evs if e.get("decision") == "BLOCKED"][:5]
        if recent_blocked:
            lines += ["", "  recent blocks:"]
            for e in recent_blocked:
                lines.append(
                    f"    {e['occurredAtUtc'][11:19]} {e['symbol']:8} "
                    f"{e['gate']:20} {e['reason'][:48]}"
                )
    text = _hdr("Today's risk-gate decisions") + "\n".join(lines) + "\n"
    color = "#ef4444" if blocked > 0 else "#1fc16b"
    html = (
        f'<h3>Risk-gate decisions today</h3>'
        f'<p>Allowed <strong style="color:#1fc16b">{allowed}</strong> · '
        f'Blocked <strong style="color:{color}">{blocked}</strong></p>'
    )
    return text, html


def _section_drift(drift: dict | None) -> tuple[str, str]:
    if not drift:
        return "", ""
    events = drift.get("drift") or []
    if not events:
        return "", ""
    counts: dict[str, int] = {}
    for e in events:
        counts[e["severity"]] = counts.get(e["severity"], 0) + 1
    has_critical = counts.get("critical", 0) > 0
    lines = [
        f"  unresolved : {len(events)}"
        + (f"  ({', '.join(f'{c} {s}' for s, c in counts.items())})"),
        "",
        "  detail:",
    ]
    for e in events[:10]:
        lines.append(
            f"    [{e['severity']:8}] {e['broker']} {e['symbol']:8} "
            f"broker={e.get('brokerQty')}  internal={e.get('internalQty')}"
        )
    text = _hdr("Position drift (broker vs internal)") + "\n".join(lines) + "\n"
    color = "#ef4444" if has_critical else "#f59e0b"
    html = (
        f'<h3 style="color:{color}">⚠ Position drift</h3>'
        f'<p>{len(events)} unresolved · '
        f'{", ".join(f"<strong>{c} {s}</strong>" for s, c in counts.items())}</p>'
    )
    return text, html


def _section_cost_honesty(cost: dict | None) -> tuple[str, str]:
    if not cost or not cost.get("hasData"):
        return "", ""
    actual = cost["actual"]
    div = cost["divergence"]
    bps_text = (
        f"  assumed   : {cost['backtestAssumption']['costBps']:.1f} bps\n"
        f"  realised  : {actual['estimatedCostBps']:.1f} bps  "
        f"(across {actual['nFills']} fills)\n"
        f"  divergence: {div['bps']:+.1f} bps  "
        f"{'MATERIAL' if div['materiallyDiverged'] else '(in line)'}\n"
    )
    text = _hdr("Cost honesty") + bps_text
    color = "#ef4444" if div["materiallyDiverged"] else "#1fc16b"
    html = (
        f'<h3>Cost honesty</h3>'
        f'<p>backtest assumes {cost["backtestAssumption"]["costBps"]:.1f}bps, '
        f'live realised <strong style="color:{color}">'
        f'{actual["estimatedCostBps"]:.1f}bps</strong> '
        f'({div["bps"]:+.1f}bps {"DIVERGED" if div["materiallyDiverged"] else "in line"})</p>'
    )
    return text, html


def _section_validation(v: dict | None) -> tuple[str, str]:
    if not v:
        return "", ""
    artifact = v.get("artifact") or {}
    in_sample = artifact.get("in_sample") or {}
    wf = (artifact.get("walk_forward") or {}).get("summary") or {}
    spy = artifact.get("spy_benchmark") or {}
    cfg = artifact.get("config") or {}
    window = f"{cfg.get('start_date', '?')} → {cfg.get('end_date', '?')}"
    text = _hdr("Strategy validation") + (
        f"  window     : {window}\n"
        f"  in-sample  : sharpe {in_sample.get('sharpe', 0):.2f}"
        f"  cagr {in_sample.get('cagr_pct', 0):.1f}%"
        f"  max-dd {in_sample.get('max_drawdown_pct', 0):.1f}%\n"
        f"  walk-fwd   : sharpe {wf.get('sharpe', 0):.2f}"
        f"  cagr {wf.get('cagr_pct', 0):.1f}%\n"
        f"  spy b&h    : sharpe {spy.get('sharpe', 0):.2f}"
        f"  cagr {spy.get('cagr_pct', 0):.1f}%"
        f"  max-dd {spy.get('max_drawdown_pct', 0):.1f}%\n"
    )
    html = (
        f'<h3>Validation</h3>'
        f'<table cellpadding="4" style="border-collapse:collapse;font-size:13px">'
        f'<thead><tr><th></th><th>Sharpe</th><th>CAGR%</th><th>MaxDD%</th></tr></thead>'
        f'<tbody>'
        f'<tr><td>In-sample</td><td>{in_sample.get("sharpe", 0):.2f}</td>'
        f'<td>{in_sample.get("cagr_pct", 0):.1f}</td>'
        f'<td>{in_sample.get("max_drawdown_pct", 0):.1f}</td></tr>'
        f'<tr><td>Walk-fwd</td><td>{wf.get("sharpe", 0):.2f}</td>'
        f'<td>{wf.get("cagr_pct", 0):.1f}</td>'
        f'<td>{wf.get("max_drawdown_pct", 0):.1f}</td></tr>'
        f'<tr><td>SPY B&H</td><td>{spy.get("sharpe", 0):.2f}</td>'
        f'<td>{spy.get("cagr_pct", 0):.1f}</td>'
        f'<td>{spy.get("max_drawdown_pct", 0):.1f}</td></tr>'
        f'</tbody></table>'
    )
    return text, html


# ---------------------------------------------------------------------- #
# Composition                                                            #
# ---------------------------------------------------------------------- #


def build_digest(strategy: str, base: str, token: str) -> EmailDigest:
    state = fetch_state(base, token, strategy)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sections_text: list[str] = []
    sections_html: list[str] = []

    for fn in [
        lambda: _section_system_state(state["system_state"]),
        lambda: _section_live_portfolio(state["live_portfolio"]),
        lambda: _section_trade_plan(state["trade_plan"]),
        lambda: _section_risk(state["risk_summary"], state["risk_events"]),
        lambda: _section_drift(state["drift"]),
        lambda: _section_cost_honesty(state["cost_feedback"]),
        lambda: _section_validation(state["validation"]),
    ]:
        try:
            t, h = fn()
        except Exception as exc:  # noqa: BLE001
            log.exception("digest section failed: %s", exc)
            t = h = ""
        if t:
            sections_text.append(t)
        if h:
            sections_html.append(h)

    text_body = (
        f"TradePro · Systematic-trading digest · {now}\n"
        f"strategy: {strategy}\n"
        + "".join(sections_text)
    )
    html_body = (
        f'<!doctype html><html><body style="font-family:system-ui,sans-serif;'
        f'max-width:760px;margin:0 auto;padding:20px;color:#1a1a1a">'
        f'<h1 style="font-size:20px">TradePro · {strategy} · {now}</h1>'
        + "".join(sections_html)
        + "</body></html>"
    )
    subject = f"TradePro algo · {strategy} · {now[:10]}"
    return EmailDigest(subject=subject, text_body=text_body, html_body=html_body)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    p = argparse.ArgumentParser(
        prog="tradepro-algo-digest",
        description=("Daily systematic-trading digest. Pulls live algo state from the API + "
                     "emails it to the trader."),
    )
    p.add_argument("--strategy", default="ichimoku_equity")
    p.add_argument("--send", action="store_true",
                   help="Send via the configured email backend (Outlook / Mail.app / Gmail SMTP).")
    p.add_argument("--print", action="store_true",
                   help="Dry-run: print the text body to stdout.")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    base = get_secret("api-base-url") or get_secret("api-url")
    token = (get_secret("api-token") or get_secret("ingest-api-token") or "")
    if not base or not token:
        log.error("missing api-base-url + api-token credentials")
        return 2

    digest = build_digest(args.strategy, base, token)

    if args.print or not args.send:
        print(digest.subject)
        print()
        print(digest.text_body)

    if args.send:
        try:
            from . import email_digest as _ed  # send_email() lives here
            # send_email loads its own cfg + recipients via the same
            # chain (secrets / settings) the long-term digest uses.
            cfg: dict[str, Any] = {}
            _ed.send_email(digest, cfg)
        except Exception as exc:  # noqa: BLE001
            log.exception("send failed: %s", exc)
            return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
