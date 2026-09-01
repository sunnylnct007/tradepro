"""The candidates digest as something worth reading.

Owner, 1 Sep 2026: *"but shdnt the email be roich as opposed to just a pure
text"*. They are right, and the plain-text version was a trade-off I made and
flagged rather than one worth keeping: the sender it replaced had charts,
support/resistance and written analysis, and losing those to gain coherence was
paying twice for one problem.

## What a card carries

    header    symbol · action · tier badge · STALE marker when past the threshold
    numbers   entry, level (strike or stop, LABELLED), the strategy's own metric
    chart     90 sessions of price with MA20/50/200, RSI and support/resistance,
              drawn from OUR bar store — the same bars the strategy screened on
    why       the strategy's one-line reason
    gates     what was checked, what it measured, what blocked — when published
    data      per-input provenance — IBKR / cache / vendor / fallback

## What it will not do

**No chart is drawn from bars we do not have.** The card renders without one and
says so, rather than showing a blank frame or — worse — a chart built from a
different source than the screen used. A chart is a claim about the data; it has
to come from the same place.

**Tier stays on every card**, not in a footnote. A candidate from a sleeve whose
backtest said DO NOT FUND must never look like one from a sleeve that passed its
gates, and a prettier email makes that MORE important, not less: presentation
lends authority.

**The text body remains the source of truth.** Every mail is multipart; a client
that shows plain text loses the charts and nothing else. The numbers are
identical in both, because two renderings that can disagree is the defect this
whole plan exists to remove.
"""
from __future__ import annotations

import datetime as _dt
import logging

log = logging.getLogger("tradepro.candidates_html")

_OK = "#0ca30c"
_WARN = "#d29922"
_BAD = "#ec835a"
_MUTED = "#8b949e"
_BG = "#0d1117"
_CARD = "#161b22"
_LINE = "#21262d"


def _bars_for(symbol: str, sessions: int = 120) -> list[dict]:
    """Bars in the chart generator's shape, from OUR store.

    The same store the strategies screened on — a chart drawn from anywhere else
    would be a different claim about the same day. Returns [] on any failure so
    the card renders chartless rather than failing the whole mail.
    """
    try:
        from .post_earnings_puts import _store
        end = _dt.datetime.now(_dt.UTC)
        start = end - _dt.timedelta(days=int(sessions * 1.6))
        frame = _store().get(
            canonical=symbol, asset_class="us_etf", resolution="1d",
            start=start, end=end, allow_partial=True, skip_fetch=True,
            fetched_by="candidates_digest")
        df = frame.df
        if df is None or df.empty:
            return []
        cols = {c.lower(): c for c in df.columns}
        out = []
        for _, r in df.iterrows():
            try:
                out.append({
                    "c": float(r[cols["close"]]),
                    "h": float(r[cols.get("high", cols["close"])]),
                    "l": float(r[cols.get("low", cols["close"])]),
                    "v": float(r[cols["volume"]]) if "volume" in cols else 0.0,
                })
            except (TypeError, ValueError, KeyError):
                continue
        return out
    except Exception as exc:  # noqa: BLE001 — a missing chart must not lose the mail
        log.debug("no bars for %s: %s", symbol, exc)
        return []


def _chart_b64(symbol: str) -> str:
    bars = _bars_for(symbol)
    if len(bars) < 20:
        return ""
    try:
        import sys
        from pathlib import Path
        # `screener/` is a sibling package, not importable by default.
        root = Path(__file__).resolve().parents[3]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from screener.chart_generator import generate_chart_b64
        return generate_chart_b64(bars, symbol) or ""
    except Exception as exc:  # noqa: BLE001
        log.debug("chart failed for %s: %s", symbol, exc)
        return ""


def _esc(s) -> str:
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _num(v, d=2, suf="") -> str:
    return f"{v:.{d}f}{suf}" if isinstance(v, (int, float)) else "—"


def _gates_html(gates: list[dict]) -> str:
    if not gates:
        return ""
    rows = ""
    for g in gates[:14]:
        passed = str(g.get("verdict", "")).lower() == "pass"
        rows += (
            f"<tr>"
            f"<td style='padding:2px 8px;color:#c9d1d9'>{_esc(g.get('gate'))}</td>"
            f"<td style='padding:2px 8px;text-align:right;color:{_MUTED}'>"
            f"{_esc(g.get('actual'))} {_esc(g.get('unit') or '')}</td>"
            f"<td style='padding:2px 8px;color:{_MUTED}'>{_esc(g.get('threshold'))}</td>"
            f"<td style='padding:2px 8px;color:{_OK if passed else _WARN};font-weight:600'>"
            f"{'pass' if passed else _esc(g.get('verdict'))}</td>"
            f"</tr>")
    return (f"<div style='margin-top:8px'><div style='font-size:10px;color:{_MUTED};"
            f"text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px'>"
            f"Gates — what was checked</div>"
            f"<table style='border-collapse:collapse;font-size:11px;width:100%'>"
            f"{rows}</table></div>")


def _prov_html(prov: list[dict]) -> str:
    if not prov:
        return ""
    weak = {"fallback", "carried", "unavailable"}
    chips = ""
    for p in prov[:8]:
        t = str(p.get("trust", ""))
        col = _WARN if t in weak else _MUTED
        chips += (f"<span style='display:inline-block;margin:2px 4px 0 0;padding:1px 6px;"
                  f"border:1px solid {col}55;border-radius:3px;font-size:10px;color:{col}'>"
                  f"{_esc(p.get('label') or p.get('input'))}: "
                  f"{_esc(p.get('source_label') or t)}</span>")
    return (f"<div style='margin-top:6px'><div style='font-size:10px;color:{_MUTED};"
            f"text-transform:uppercase;letter-spacing:.06em'>Data — where it came from</div>"
            f"{chips}</div>")


def _card(c: dict, stale_hours: float, with_charts: bool) -> str:
    age = c.get("_age_h")
    stale = age is not None and age > stale_hours
    tier = c.get("tier") or "?"
    tier_col = _OK if tier == "gated" else _WARN

    chart = _chart_b64(c.get("symbol", "")) if with_charts else ""
    chart_html = (
        f"<img src='data:image/png;base64,{chart}' alt='{_esc(c.get('symbol'))}' "
        f"style='width:100%;border-radius:4px;margin:8px 0'/>"
        if chart else
        f"<div style='margin:8px 0;padding:8px;border:1px dashed {_LINE};border-radius:4px;"
        f"font-size:11px;color:{_MUTED}'>No chart — our bar store has too little history "
        f"for this symbol. Drawing one from another source would be a different claim "
        f"about the same day.</div>")

    lvl = (f"{_num(c.get('level'))} <span style='color:{_MUTED};font-size:11px'>"
           f"{_esc(c.get('level_label'))}</span>") if c.get("level") is not None else "—"
    met = (f"{_num(c.get('metric'), 1)}<span style='color:{_MUTED};font-size:11px'>"
           f"{_esc(c.get('metric_label'))}</span>") if c.get("metric") is not None else "—"

    return f"""
    <div style='background:{_CARD};border:1px solid {_LINE};border-radius:8px;
                padding:12px 14px;margin-bottom:12px'>
      <div style='display:flex;align-items:baseline;gap:8px;flex-wrap:wrap'>
        <span style='font-size:17px;font-weight:700;color:#e6edf3;
                     font-family:ui-monospace,monospace'>{_esc(c.get('symbol'))}</span>
        <span style='color:{_MUTED};font-size:13px'>{_esc(c.get('action'))}</span>
        <span style='font-size:10px;padding:1px 6px;border-radius:3px;
                     color:{tier_col};border:1px solid {tier_col}66'>{_esc(tier)}</span>
        {f"<span style='font-size:10px;padding:1px 6px;border-radius:3px;color:{_BAD};"
         f"border:1px solid {_BAD}66'>STALE {age:.0f}h</span>" if stale else ""}
      </div>
      <table style='margin-top:8px;border-collapse:collapse;font-size:13px;width:100%'>
        <tr>
          <td style='padding:3px 0;color:{_MUTED};width:33%'>Entry</td>
          <td style='padding:3px 0;color:{_MUTED};width:33%'>Level</td>
          <td style='padding:3px 0;color:{_MUTED}'>Rank</td>
        </tr>
        <tr style='font-family:ui-monospace,monospace;color:#e6edf3'>
          <td style='padding:1px 0'>{_num(c.get('entry'))}</td>
          <td style='padding:1px 0'>{lvl}</td>
          <td style='padding:1px 0'>{met}</td>
        </tr>
      </table>
      {chart_html}
      <div style='font-size:12px;color:{_MUTED};line-height:1.5'>{_esc(c.get('why'))}</div>
      {_gates_html(c.get('gates') or [])}
      {_prov_html(c.get('provenance') or [])}
    </div>"""


def build_html(rows: list[dict], problems: list[str], now: _dt.datetime,
               stale_hours: float, with_charts: bool = True,
               holdings_html: str = "") -> str:
    """The rich body. `rows` must already be sorted and grouped by the caller."""
    parts = [
        f"<div style='background:{_BG};padding:16px;font-family:-apple-system,"
        f"BlinkMacSystemFont,Segoe UI,sans-serif;color:#e6edf3'>",
        f"<div style='font-size:19px;font-weight:700'>TradePro candidates</div>",
        f"<div style='font-size:12px;color:{_MUTED};margin-bottom:14px'>"
        f"{now:%Y-%m-%d %H:%M}Z · CANDIDATES FOR MANUAL USE — nothing here is "
        f"placed automatically.</div>",
    ]

    if not rows:
        parts.append(
            f"<div style='background:{_CARD};border:1px solid {_LINE};border-radius:8px;"
            f"padding:14px;font-size:13px;line-height:1.6'>"
            f"<b>No candidates today.</b><br/>"
            f"<span style='color:{_MUTED}'>That is a verdict, not a failure — these "
            f"strategies fire on a minority of sessions by design, and a quiet day is "
            f"the rules working. A day with nothing is not the same as a day nobody "
            f"screened; anything that failed to load is listed below.</span></div>")
    else:
        cur = None
        for c in rows:
            if c.get("strategy") != cur:
                cur = c.get("strategy")
                tier = c.get("tier") or "?"
                note = ("passed its pre-registered gates" if tier == "gated"
                        else "NOT proven — for your judgement, not for size")
                parts.append(
                    f"<div style='margin:18px 0 8px;font-size:13px;font-weight:600'>"
                    f"{_esc(cur)} <span style='font-weight:400;color:{_MUTED};"
                    f"font-size:12px'>— {note}</span></div>")
            parts.append(_card(c, stale_hours, with_charts))

    if holdings_html:
        parts.append(holdings_html)

    if problems:
        items = "".join(f"<li style='margin:2px 0'>{_esc(p)}</li>" for p in problems)
        parts.append(
            f"<div style='margin-top:16px;background:{_CARD};border:1px solid {_BAD}55;"
            f"border-radius:8px;padding:12px 14px;font-size:12px'>"
            f"<b style='color:{_BAD}'>Could not load</b>"
            f"<div style='color:{_MUTED};margin:3px 0 6px'>This is NOT the same as "
            f"“no candidates” — a strategy missing without saying so is "
            f"indistinguishable from one with nothing to show.</div>"
            f"<ul style='margin:0;padding-left:18px;color:{_MUTED}'>{items}</ul></div>")

    parts.append(
        f"<div style='margin-top:16px;font-size:11px;color:{_MUTED};line-height:1.6'>"
        f"Rows older than {stale_hours:.0f}h are marked STALE — freshness is per ROW, "
        f"because each strategy publishes on its own schedule.<br/>"
        f"Charts are 90 sessions from our own bar store, the same bars the strategy "
        f"screened on.<br/>"
        f"Board: <a href='http://16.60.201.137/' style='color:{_OK}'>the desk</a> → "
        f"Candidates</div></div>")
    return "".join(parts)


def _holdings_html(holdings: list[dict], mode: str | None) -> str:
    """Your actual book, beside today's candidates.

    MOVED HERE from the nightly digest (2 Sep 2026), which is being retired.
    Owner: "again this email which adds no value".

    That digest was structurally empty and not by accident: across all 14
    compare universes it scored 1,778 rows and bucketed 1,596 WAIT, 175 AVOID
    and SEVEN BUY — and the verification gate then suppressed those, because 196
    rows carried EARNINGS_UNKNOWN or EARNINGS_UNVERIFIED. A screen that says
    WAIT to 90% of everything will keep producing zero most nights.

    Its candidate half is superseded by this digest, which covers every strategy
    with tier, freshness, gates and provenance — and in a SEVENTH vocabulary
    (BUY/WAIT/AVOID) that matched nothing else on the desk.

    This chart was the one thing in it that existed nowhere else, so it comes
    across rather than dying with it. Candidates answer "what could I do today";
    holdings answer "how is what I already did going". One email, both.
    """
    if not holdings:
        return ""
    try:
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tradepro_strategies.email_charts import holdings_pnl_bar_png
        b64 = holdings_pnl_bar_png(holdings)
    except Exception as exc:  # noqa: BLE001 — never lose the mail over a chart
        log.debug("holdings chart failed: %s", exc)
        b64 = ""
    if not b64:
        return ""
    tag = f" · {_esc(mode)}" if mode else ""
    return (f"<div style='margin:20px 0 6px;font-size:13px;font-weight:600'>"
            f"Your holdings<span style='font-weight:400;color:{_MUTED};font-size:12px'>"
            f" — unrealised P&amp;L{tag}</span></div>"
            f"<div style='background:{_CARD};border:1px solid {_LINE};border-radius:8px;"
            f"padding:10px'>"
            f"<img src='data:image/png;base64,{b64}' alt='holdings' "
            f"style='width:100%;border-radius:4px'/></div>")
