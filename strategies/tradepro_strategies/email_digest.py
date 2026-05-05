"""Build the daily TradePro email digest from compare payloads.

The digest is a pure function — given the universe payloads (each
matching the shape pushed to /api/ingest/compare), it returns an
EmailDigest with subject, plain-text body, and HTML body. Reads no
files, opens no sockets — keeps it behave-testable without mocks.

Sending lives in send.py / cli/email_digest.py so this module can
be reused by anything that wants to build the same digest (a future
in-app notifications page, Slack adapters, etc.) without dragging
SMTP transport in.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from typing import Any


@dataclass
class EmailDigest:
    subject: str
    text_body: str
    html_body: str

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "text_body": self.text_body,
            "html_body": self.html_body,
        }


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _latest_data_date(payloads: list[dict]) -> str | None:
    """Walk every row's market_state.as_of and return the latest YYYY-MM-DD
    seen. None when the data is empty or carries no as_of stamps."""
    latest: str | None = None
    for env in payloads:
        rows = env.get("payload", {}).get("rows") or env.get("rows") or []
        for r in rows:
            ms = r.get("market_state") or {}
            as_of = ms.get("as_of")
            if not as_of:
                continue
            stamp = as_of[:10]
            if latest is None or stamp > latest:
                latest = stamp
    return latest


_BOX_WIDTH = 68  # interior width between the ║ borders


def _box_line(text: str) -> str:
    """One row of the summary box — pads/truncates the interior to
    _BOX_WIDTH so the right border always lines up."""
    interior = text[: _BOX_WIDTH].ljust(_BOX_WIDTH)
    return f"║{interior}║"


def _summary_card(
    buys: list[dict], waits: list[dict], avoids: list[dict],
    holdings: list[dict] | None,
    banner: str | None,
) -> str:
    """Top-of-email stat block with box-drawing borders. Quick read
    of what's in the digest before scanning the per-row cards."""
    today = _now_str()
    rule_top = "╔" + "═" * _BOX_WIDTH + "╗"
    rule_mid = "╠" + "═" * _BOX_WIDTH + "╣"
    rule_bot = "╚" + "═" * _BOX_WIDTH + "╝"
    lines = [
        rule_top,
        _box_line(f" TRADEPRO DAILY DIGEST · {today}"),
        rule_mid,
        _box_line(
            f"  BUY {len(buys):3d}   WAIT {len(waits):3d}   AVOID {len(avoids):3d}"
        ),
    ]
    if holdings:
        total = 0.0
        for h in holdings:
            v = h.get("unrealisedAbs")
            if isinstance(v, (int, float)):
                total += v
        ccy_set = {h.get("currency") for h in holdings if h.get("currency")}
        ccy = next(iter(ccy_set), "") if len(ccy_set) == 1 else "mixed"
        sign = "+" if total >= 0 else ""
        lines.append(_box_line(
            f"  HOLDINGS: {len(holdings):2d} positions · "
            f"unrealised {sign}{total:.2f} {ccy}"
        ))
    if banner:
        for chunk in _wrap(banner, _BOX_WIDTH - 4):
            lines.append(_box_line(f"  {chunk}"))
    lines.append(rule_bot)
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    out: list[str] = []
    line = ""
    for word in text.split():
        if len(line) + 1 + len(word) <= width:
            line = (line + " " + word).strip()
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    return out


def _top_n_bar_chart(items: list[dict], n: int = 5) -> str:
    """ASCII horizontal bar chart of the top-N items by Sharpe.
    Read at-a-glance ranking without scrolling through each card."""
    candidates = [it for it in items if isinstance(it.get("sharpe"), (int, float))]
    if not candidates:
        return ""
    candidates.sort(key=lambda it: it.get("sharpe") or 0, reverse=True)
    top = candidates[:n]
    if not top:
        return ""
    sym_width = max(8, max(len(it["symbol"]) for it in top))
    lo = min(it["sharpe"] for it in top)
    hi = max(it["sharpe"] for it in top)
    rng = hi - lo if hi > lo else max(abs(hi), 0.01)
    lines = [f"Top {len(top)} BUY by Sharpe (best → worst):"]
    bar_width = 24
    for it in top:
        sharpe = it["sharpe"]
        filled = int((sharpe - lo) / rng * bar_width) if rng else bar_width
        filled = max(1, filled)
        bar = "█" * filled + "·" * (bar_width - filled)
        sym = it["symbol"].ljust(sym_width)
        lines.append(f"  {sym}  │{bar}│ Sharpe {sharpe:+.2f}")
    return "\n".join(lines)


def _staleness_banner(payloads: list[dict]) -> str | None:
    """Returns a one-line note when the data is older than today.
    Lets the digest ship on bank holidays / weekends without
    pretending that today's prices exist. None when data is fresh."""
    latest = _latest_data_date(payloads)
    if not latest:
        return None
    today = _now_str()
    if latest >= today:
        return None
    return (
        f"Data as of {latest} — markets closed today (bank holiday / "
        f"weekend or run before today's close)."
    )


def _best_row_for_symbol(rows: list[dict]) -> dict | None:
    """The comparator pushes one row per (symbol, strategy). Pick the
    rank-1 row for each symbol — that's what the Compare page shows."""
    if not rows:
        return None
    return min(rows, key=lambda r: r.get("rank", 1e9))


def _group_by_symbol(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        sym = r.get("symbol")
        if sym:
            out.setdefault(sym, []).append(r)
    return out


def _filter_bucket(payloads: list[dict], bucket: str) -> list[dict]:
    """Walk every universe payload, return one summary row per symbol
    whose best-rank row matches the requested bucket."""
    seen: set[str] = set()
    items: list[dict] = []
    for env in payloads:
        universe = env.get("universe") or env.get("payload", {}).get("universe", "")
        rows = env.get("payload", {}).get("rows") or env.get("rows") or []
        for sym, sym_rows in _group_by_symbol(rows).items():
            if sym in seen:
                continue
            best = _best_row_for_symbol(sym_rows)
            if not best or best.get("bucket") != bucket:
                continue
            seen.add(sym)
            ms = best.get("market_state") or {}
            stats = best.get("stats") or {}
            sentiment = best.get("sentiment_summary") or {}
            # Per-strategy long/flat map for the consensus dot row.
            strategy_states = [
                {"name": r.get("strategy"), "in_position": bool(r.get("in_position"))}
                for r in sorted(sym_rows, key=lambda r: r.get("strategy") or "")
            ]
            items.append({
                "symbol": sym,
                "universe": universe,
                "bucket": best.get("bucket"),
                "bucket_reason": best.get("bucket_reason"),
                "long_count": sum(1 for r in sym_rows if r.get("in_position")),
                "total_strategies": len(sym_rows),
                "strategy": best.get("strategy"),
                "strategy_states": strategy_states,
                # Price + reference levels with full context
                "last_price": ms.get("last_price"),
                "currency": best.get("currency"),
                "as_of": ms.get("as_of"),
                "rsi_14": ms.get("rsi_14"),
                "above_sma_200": ms.get("above_sma_200"),
                "sma_200": ms.get("sma_200"),
                "pct_off_52w_high_pct": ms.get("pct_off_52w_high_pct"),
                "pct_off_52w_high_date": ms.get("pct_off_52w_high_date"),
                "pct_off_52w_high_price": ms.get("pct_off_52w_high_price"),
                "drawdown_from_peak_pct": ms.get("drawdown_from_peak_pct"),
                "peak_date": ms.get("peak_date"),
                "peak_price": ms.get("peak_price"),
                "momentum_3m_pct": ms.get("momentum_3m_pct"),
                "momentum_12m_pct": ms.get("momentum_12m_pct"),
                "vol_30d_annual_pct": ms.get("vol_30d_annual_pct"),
                # Performance stats
                "cagr_pct": stats.get("cagr_pct"),
                "sharpe": stats.get("sharpe"),
                "max_drawdown_pct": stats.get("max_drawdown_pct"),
                "max_drawdown_recovery_days": stats.get("max_drawdown_recovery_days"),
                "max_drawdown_still_recovering": stats.get("max_drawdown_still_recovering"),
                # Multi-family signals
                "cross_sectional_momentum": best.get("cross_sectional_momentum"),
                "valuation_flag": best.get("valuation_flag"),
                "earnings_signal": best.get("earnings_signal"),
                "swing_score": best.get("swing_score"),
                # Sentiment summary
                "sentiment_mean_7d": sentiment.get("mean_sentiment"),
                "sentiment_material_negative_count": sentiment.get("material_negative_count"),
                "sentiment_status": best.get("sentiment_status"),
                "sentiment_demoted": best.get("sentiment_demoted"),
                # Decision trace for evidence
                "decision_trace": ms.get("decision_trace") or [],
            })
    return items


def _fmt(value: Any, suffix: str = "", digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def _format_dd_recovery(item: dict) -> str:
    """Compact "−22% (recovered 480d)" or "−22% (still recovering, 142d
    in)" — keeps the recovery-time stat visible per the design review."""
    dd = item.get("max_drawdown_pct")
    if dd is None:
        return "—"
    base = _fmt(dd, "%", 1)
    days = item.get("max_drawdown_recovery_days")
    still = item.get("max_drawdown_still_recovering")
    if still:
        return f"{base} (still recovering)"
    if days is not None:
        return f"{base} (recovered {int(days)}d)"
    return base


def _ascii_sparkline(values: list[float] | None) -> str:
    """Compact 8-block Unicode bar chart of recent values. Plain text
    by design — renders consistently across mail clients without HTML.
    Returns empty string when there's nothing to plot."""
    if not values or len(values) < 2:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    rng = hi - lo if hi > lo else 1.0
    return "".join(blocks[min(7, max(0, int((v - lo) / rng * 7)))] for v in values)


def _consensus_dots(states: list[dict]) -> str:
    """Render the per-strategy long/flat votes as a single line:
        sma_crossover ●  rsi_meanrev ○  macd ●  donchian ●  bnh ●
    ● = currently long, ○ = currently flat. Compact alias for each
    strategy name."""
    aliases = {
        "sma_crossover": "sma",
        "rsi_mean_reversion": "rsi",
        "macd_signal_cross": "macd",
        "donchian_breakout": "donchian",
        "buy_and_hold": "bnh",
    }
    bits = []
    for s in states:
        name = aliases.get(s.get("name", ""), s.get("name", ""))
        marker = "●" if s.get("in_position") else "○"
        bits.append(f"{name}{marker}")
    return "  ".join(bits)


def _format_cross_basket(item: dict) -> str | None:
    """Compact one-liner for the cross-basket signals — Family-2
    valuation flag and Family-3 momentum rank. Returns None when
    neither is present so the caller can skip the line. Format:

        Momentum rank 3/13 (top quartile) · Valuation cheap

    Each piece independently optional — partial data still produces
    something useful."""
    cs = item.get("cross_sectional_momentum") or {}
    val = item.get("valuation_flag") or {}
    parts: list[str] = []
    if cs.get("rank") is not None:
        peer_count = cs.get("peer_count")
        if isinstance(peer_count, int):
            total = peer_count + 1
            piece = f"Momentum rank {cs['rank']}/{total}"
        else:
            piece = f"Momentum rank {cs['rank']}"
        if cs.get("is_top_quartile"):
            piece += " (top quartile)"
        parts.append(piece)
    flag = val.get("flag")
    if flag in ("cheap", "expensive"):
        parts.append(f"Valuation {flag}")
    return " · ".join(parts) if parts else None


def _text_block(items: list[dict], heading: str) -> str:
    """Detailed per-row evidence card. Each row reads top-to-bottom
    like a research note — price + reference levels, the full
    consensus dots, multi-family signals, regime-tested stats, and
    the citable bucket reason. Designed for plain text but with
    enough visual hierarchy that an investor can skim 10 BUY rows
    in 60 seconds and pick the 2-3 worth a deeper look."""
    lines = [heading, "─" * len(heading)]
    if not items:
        lines.append("(none)")
        return "\n".join(lines)
    for it in items:
        sym = it["symbol"]
        univ = it.get("universe") or "—"
        bucket = it["bucket"]
        long = it.get("long_count")
        total = it.get("total_strategies")
        consensus = f"{long}/{total}" if long is not None and total else "—"
        # ── Header line ──────────────────────────────────────────
        lines.append(f"┌─ {sym}  ·  {univ}  ·  {bucket}  ·  {consensus} long")
        if it.get("bucket_reason"):
            lines.append(f"│  WHY: {it['bucket_reason']}")
        # Swing composite score — the headline 0-8 reading that
        # combines all four families (quality / valuation / event /
        # price). Sits right under WHY so the user sees both the
        # rule-engine bucket AND the multi-family score next to each
        # other (they can disagree — that's the design).
        sw = it.get("swing_score") or {}
        if sw.get("total") is not None:
            layers = sw.get("layers") or {}
            layer_str = (
                f"Q{layers.get('quality', 0)}·"
                f"V{layers.get('valuation', 0)}·"
                f"E{layers.get('event', 0)}·"
                f"P{layers.get('price', 0)}"
            )
            lines.append(
                f"│  SWING:  {sw['total']}/8  →  {sw.get('verdict', '')}  "
                f"[{layer_str}]"
            )
        lines.append("│")

        # ── Price + reference levels ────────────────────────────
        ccy = it.get("currency") or ""
        last = _fmt(it.get("last_price"), "", 2)
        sma_200 = _fmt(it.get("sma_200"), "", 2)
        above = it.get("above_sma_200")
        trend_label = "above" if above is True else ("below" if above is False else "—")
        lines.append(f"│  PRICE     {last} {ccy}  (as of {(it.get('as_of') or '')[:10]})")
        lines.append(f"│  TREND     {trend_label} 200d SMA ({sma_200})")

        # 52w-high reference with date
        off_high = _fmt(it.get("pct_off_52w_high_pct"), "%", 1)
        high_date = (it.get("pct_off_52w_high_date") or "")[:10]
        high_price = _fmt(it.get("pct_off_52w_high_price"), "", 2)
        if high_date:
            lines.append(f"│  52W HIGH  {high_price} on {high_date}  (today: -{off_high} off)")

        # 5y peak as the long-term valuation reference
        dd = it.get("drawdown_from_peak_pct")
        peak_date = (it.get("peak_date") or "")[:10]
        peak_price = _fmt(it.get("peak_price"), "", 2)
        if dd is not None and peak_date:
            dd_str = _fmt(dd, "%", 1)
            lines.append(f"│  5Y PEAK   {peak_price} on {peak_date}  (today: {dd_str} from peak)")

        # ── Indicators ──────────────────────────────────────────
        rsi = _fmt(it.get("rsi_14"), digits=0)
        mom_3m = _fmt(it.get("momentum_3m_pct"), "%", 1)
        mom_12m = _fmt(it.get("momentum_12m_pct"), "%", 1)
        vol = _fmt(it.get("vol_30d_annual_pct"), "%", 1)
        lines.append(
            f"│  INDICATORS  RSI {rsi}  ·  3m mom {mom_3m}  ·  "
            f"12m mom {mom_12m}  ·  vol {vol}"
        )

        # ── Strategy consensus (full dot row) ──────────────────
        states = it.get("strategy_states") or []
        if states:
            lines.append(f"│  CONSENSUS  {_consensus_dots(states)}")

        # ── Cross-basket signals ───────────────────────────────
        cross = _format_cross_basket(it)
        if cross:
            lines.append(f"│  PEERS     {cross}")

        # ── Earnings signal (Family 4) ─────────────────────────
        ev = it.get("earnings_signal") or {}
        verdict = ev.get("verdict")
        if verdict and verdict not in ("NO_RECENT", "NOT_APPLICABLE"):
            ev_data = ev.get("earnings") or {}
            surprise = ev_data.get("surprise_pct")
            days_since = ev.get("days_since_earnings")
            days_left = ev.get("days_remaining_in_window")
            retreat = ev.get("retreat_from_post_earnings_peak_pct")
            ev_bits = [verdict.lower().replace("_", " ")]
            if surprise is not None:
                ev_bits.append(f"beat {surprise:+.1f}%")
            if days_since is not None:
                ev_bits.append(f"day {days_since}/60")
            if retreat is not None:
                ev_bits.append(f"retreat {retreat:.1f}%")
            lines.append(f"│  EARNINGS  {' · '.join(ev_bits)}")
        # ── Upcoming earnings (Finnhub) ────────────────────────
        upcoming = ev.get("upcoming") or {}
        days_until = upcoming.get("days_until")
        if days_until is not None:
            warn = "⚠ " if days_until <= 14 else ""
            hour = upcoming.get("hour") or ""
            hour_text = f" ({hour})" if hour in ("bmo", "amc") else ""
            est = upcoming.get("eps_estimate")
            est_text = f", EPS est {est:.2f}" if isinstance(est, (int, float)) else ""
            lines.append(
                f"│  NEXT EPS  {warn}reports in {days_until}d on "
                f"{upcoming.get('date', '')}{hour_text}{est_text}"
            )

        # ── Sentiment ──────────────────────────────────────────
        sent_mean = it.get("sentiment_mean_7d")
        sent_neg = it.get("sentiment_material_negative_count")
        sent_status = it.get("sentiment_status")
        if sent_status and sent_status != "no_news":
            mean_str = _fmt(sent_mean, digits=2) if sent_mean is not None else "—"
            neg = sent_neg if sent_neg is not None else 0
            lines.append(
                f"│  NEWS 7D   mean sentiment {mean_str}, "
                f"{neg} material-negative ({sent_status})"
            )
            if it.get("sentiment_demoted"):
                lines.append("│             (BUY → WAIT demotion fired)")

        # ── Performance stats ──────────────────────────────────
        cagr = _fmt(it.get("cagr_pct"), "%", 1)
        sharpe = _fmt(it.get("sharpe"), digits=2)
        max_dd = _format_dd_recovery(it)
        lines.append(
            f"│  STATS     {it.get('strategy', '')}: "
            f"CAGR {cagr} · Sharpe {sharpe} · MaxDD {max_dd}"
        )
        lines.append("└─")
        lines.append("")
    return "\n".join(lines).rstrip()


def _html_block(items: list[dict], heading: str, accent: str) -> str:
    rows = []
    for it in items:
        rsi = _fmt(it.get("rsi_14"), digits=0)
        off_high = _fmt(it.get("pct_off_52w_high_pct"), "%", 1)
        cagr = _fmt(it.get("cagr_pct"), "%", 1)
        sharpe = _fmt(it.get("sharpe"), digits=2)
        max_dd = _format_dd_recovery(it)
        long = it.get("long_count")
        total = it.get("total_strategies")
        consensus = f"{long}/{total}" if long is not None and total else "—"
        rows.append(
            f"<tr>"
            f"<td><b>{escape(it['symbol'])}</b></td>"
            f"<td>{escape(it.get('universe') or '—')}</td>"
            f"<td>{consensus}</td>"
            f"<td>{escape(rsi)}</td>"
            f"<td>{escape(off_high)}</td>"
            f"<td>{escape(cagr)}</td>"
            f"<td>{escape(sharpe)}</td>"
            f"<td>{escape(max_dd)}</td>"
            f"</tr>"
            f"<tr><td colspan=8 style='color:#666;font-size:11px;padding-bottom:8px'>"
            f"{escape(it.get('bucket_reason') or '')}</td></tr>"
        )
    body = "".join(rows) if rows else (
        "<tr><td colspan=8 style='color:#999'>(none)</td></tr>"
    )
    return (
        f"<h3 style='border-left:3px solid {accent};padding-left:8px;margin-top:24px'>"
        f"{escape(heading)}</h3>"
        f"<table style='border-collapse:collapse;font-size:13px;width:100%'>"
        f"<thead><tr style='color:#666;text-align:left;font-size:11px'>"
        f"<th>Symbol</th><th>Universe</th><th>Long</th><th>RSI</th>"
        f"<th>52w</th><th>CAGR</th><th>Sharpe</th><th>MaxDD</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _row_for_symbol(symbol: str, payloads: list[dict]) -> dict | None:
    """Best-rank compare row for a symbol across all payloads —
    returns the FULL row (market_state, swing_score, etc.), not the
    summary the digest cards render. Used by the Phase-2 holdings
    analyser. None when symbol isn't tracked in any universe."""
    if not symbol:
        return None
    target = symbol.upper()
    best_match: dict | None = None
    best_rank = 1e9
    for env in payloads:
        rows = env.get("payload", {}).get("rows") or env.get("rows") or []
        for r in rows:
            if (r.get("symbol") or "").upper() != target:
                continue
            rank = r.get("rank") or 1e9
            if rank < best_rank:
                best_rank = rank
                best_match = r
    return best_match


def _verdict_for_symbol(symbol: str, payloads: list[dict]) -> dict | None:
    """Look up a symbol's best-rank row across all compare payloads,
    return the bucket + reason + swing composite. Used to cross-
    reference T212 holdings against today's verdict so the digest
    can highlight 'YOU OWN THIS, system says X today'. None when
    the symbol isn't in any tracked universe."""
    if not symbol:
        return None
    target = symbol.upper()
    best_match: dict | None = None
    best_universe = ""
    best_rank = 1e9
    for env in payloads:
        universe = env.get("payload", {}).get("universe") or env.get("universe", "")
        rows = env.get("payload", {}).get("rows") or env.get("rows") or []
        for r in rows:
            if (r.get("symbol") or "").upper() != target:
                continue
            rank = r.get("rank") or 1e9
            if rank < best_rank:
                best_rank = rank
                best_match = r
                best_universe = universe
    if not best_match:
        return None
    ms = best_match.get("market_state") or {}
    return {
        "symbol": target,
        "universe": best_universe,
        "bucket": best_match.get("bucket"),
        "bucket_reason": best_match.get("bucket_reason"),
        "rsi_14": ms.get("rsi_14"),
        "pct_off_52w_high_pct": ms.get("pct_off_52w_high_pct"),
        "swing_score": best_match.get("swing_score"),
    }


def _format_holdings_block(holdings: list[dict], payloads: list[dict]) -> str:
    """Render the 'What You Hold' section — what's in your T212
    portfolio, what each position cost vs is now, and what today's
    verdict says about it."""
    if not holdings:
        return ""
    lines = [
        "What You Hold (T212)",
        "─" * 20,
        "Cross-references each position against today's verdict — so you",
        "can see whether the system thinks now is the time to add, hold,",
        "or trim what you already own.",
        "",
    ]
    for h in holdings:
        ticker = h.get("ticker") or "—"
        yahoo_sym = h.get("yahooSymbol") or h.get("yahoo_symbol")
        name = h.get("instrumentName") or "—"
        qty = _fmt(h.get("quantity"), digits=4)
        ccy = h.get("currency") or ""
        avg = _fmt(h.get("averagePricePaid"), digits=2)
        cur = _fmt(h.get("currentPrice"), digits=2)
        upct = _fmt(h.get("unrealisedPct"), "%", 2)
        uabs = _fmt(h.get("unrealisedAbs"), digits=2)
        upct_raw = h.get("unrealisedPct")
        sign = "+" if upct_raw is not None and upct_raw >= 0 else ""

        verdict = _verdict_for_symbol(yahoo_sym or "", payloads) if yahoo_sym else None

        lines.append(f"┌─ {name} ({ticker})")
        lines.append(
            f"│  POSITION  {qty} shares @ avg {avg} {ccy} → now {cur} {ccy}"
        )
        lines.append(
            f"│  P&L       {sign}{upct} ({sign}{uabs} {ccy})"
        )
        if verdict:
            lines.append(
                f"│  TODAY     {verdict['bucket']} per {verdict['universe']} compare"
            )
            if verdict.get("bucket_reason"):
                lines.append(f"│              {verdict['bucket_reason']}")
            # Phase-X composite swing score — the multi-family number
            # next to the rule-engine bucket. They can disagree (by
            # design) so the user sees both lenses.
            sw = verdict.get("swing_score") or {}
            if sw.get("total") is not None:
                layers = sw.get("layers") or {}
                lines.append(
                    f"│  SWING     {sw['total']}/8 → {sw.get('verdict', '')}  "
                    f"[Q{layers.get('quality',0)}·V{layers.get('valuation',0)}"
                    f"·E{layers.get('event',0)}·P{layers.get('price',0)}]"
                )
            # Phase-2 holdings recommendation: BUY_MORE / HOLD / TRIM
            # with concrete narrative. Sees market_state + swing_score
            # off the full row, so the prose can quote RSI / cost-basis
            # / new-cost-after-tranche. Replaces the old _holdings_
            # action_hint string.
            from .holdings import analyse_holding
            full_row = _row_for_symbol(yahoo_sym or "", payloads) if yahoo_sym else None
            rec = analyse_holding(h, full_row)
            # Position-into-earnings warning. When Finnhub flagged an
            # upcoming announcement within ~14 days, surface it
            # ABOVE the action hint — pre-earnings vol can override
            # the structural recommendation.
            if full_row:
                upc = (full_row.get("earnings_signal") or {}).get("upcoming") or {}
                du = upc.get("days_until")
                if isinstance(du, int) and du <= 14:
                    hour = upc.get("hour") or ""
                    hour_text = f" ({hour})" if hour in ("bmo", "amc") else ""
                    lines.append(
                        f"│  ⚠ EPS     reports in {du}d on "
                        f"{upc.get('date', '')}{hour_text} — expect volatility"
                    )
            lines.append(f"│  ACTION    {rec.action}")
            lines.append(f"│  WHY       {rec.narrative}")
            if rec.avg_cost_after_equal_tranche is not None:
                lines.append(
                    f"│             new cost after equal tranche: "
                    f"{rec.avg_cost_after_equal_tranche:.2f} {ccy}"
                )
        else:
            from .holdings import analyse_holding
            rec = analyse_holding(h, None)
            lines.append(
                f"│  TODAY     not in any tracked universe — run "
                f"`evaluate_symbols(\"{yahoo_sym or ticker}\")` for an ad-hoc verdict"
            )
            lines.append(f"│  ACTION    {rec.action}")
            lines.append(f"│  WHY       {rec.narrative}")
        lines.append("└─")
        lines.append("")
    return "\n".join(lines).rstrip()


def _holdings_action_hint(
    bucket: str | None,
    unrealised_pct: float | None,
    *,
    swing_total: int | None = None,
) -> str:
    """Plain-English action hint combining today's bucket with the
    user's current P&L on the position. Conservative — never says
    'sell' explicitly; uses 'consider trimming' / 'hold' / 'add'
    language so the hint is a prompt, not a directive.

    When `swing_total` is supplied, it sharpens the hint: a STRONG_BUY
    bucket paired with a low swing score is less convincing than the
    same bucket with a 7/8 composite, and the prose says so.

    Caveat: this is rule-based on (bucket, swing, P&L) — still NOT
    horizon-weighted. Phase 2 (portfolio-aware engine) layers
    user-supplied horizon + cost-basis state on top."""
    swing_qualifier = ""
    if swing_total is not None:
        if swing_total >= 6:
            swing_qualifier = " (swing composite agrees strongly, ≥6/8)"
        elif swing_total >= 4:
            swing_qualifier = " (swing composite supports, 4-5/8)"
        elif swing_total >= 2:
            swing_qualifier = " (swing composite mixed, 2-3/8)"
        else:
            swing_qualifier = " (swing composite weak, ≤1/8)"

    if bucket == "AVOID":
        if unrealised_pct is not None and unrealised_pct < -10.0:
            return f"AVOID + position down >10%: consider exit; trend is broken{swing_qualifier}"
        return f"AVOID: do not add; consider trimming on strength{swing_qualifier}"
    if bucket == "WAIT":
        if unrealised_pct is not None and unrealised_pct > 15.0:
            return f"WAIT + position up >15%: consider taking partial profits{swing_qualifier}"
        return f"WAIT: hold what you have; don't add until trend confirms{swing_qualifier}"
    if bucket == "BUY":
        if unrealised_pct is not None and unrealised_pct < -5.0:
            return f"BUY + position down: classic average-down zone{swing_qualifier}"
        return f"BUY: structurally fine to add on weakness{swing_qualifier}"
    return "no clear hint without a current verdict"


def build_digest(
    payloads: list[dict],
    *,
    holdings: list[dict] | None = None,
) -> EmailDigest:
    """Build the digest. `payloads` is a list of compare envelopes —
    one per universe — matching the shape the API returns from
    /api/compare/latest. `holdings` is the optional T212 positions
    list; when supplied, a 'What You Hold' section appears at the
    top with each position cross-referenced against today's verdict."""
    buys = _filter_bucket(payloads, "BUY")
    avoids = _filter_bucket(payloads, "AVOID")
    waits = _filter_bucket(payloads, "WAIT")
    today = _now_str()

    subject = (
        f"TradePro Digest {today} — {len(buys)} BUY · "
        f"{len(waits)} WAIT · {len(avoids)} AVOID"
    )

    banner = _staleness_banner(payloads)
    summary = _summary_card(buys, waits, avoids, holdings or [], banner)
    bar_chart = _top_n_bar_chart(buys, n=5)
    holdings_block = _format_holdings_block(holdings or [], payloads)
    sections = [summary]
    if bar_chart:
        sections.append(bar_chart)
    if holdings_block:
        sections.append(holdings_block)
    sections.extend([
        f"BUY candidates ({len(buys)})",
        _text_block(buys, "BUY") if buys else "(none today)",
        f"AVOID ({len(avoids)})",
        _text_block(avoids, "AVOID") if avoids else "(none today)",
        f"WAIT ({len(waits)})",
        _text_block(waits, "WAIT") if waits else "(none today)",
        "Verdicts come from the rule engine + multi-strategy vote;\n"
        "every number traces to a structured fact in the API.\n"
        "For the full HTML version with colour + tables, run:\n"
        "  uv run tradepro-email --save-html ~/digest.html\n"
        "and open the file in your browser.",
    ])
    text_body = "\n\n".join(sections)

    banner_html = (
        f"<div style='background:#fff7e0;border-left:3px solid #e8a23a;"
        f"padding:8px 12px;margin-bottom:16px;font-size:12px;color:#7a4f00'>"
        f"{escape(banner)}</div>"
    ) if banner else ""
    html_body = (
        f"<div style='font-family:-apple-system,Helvetica,sans-serif;max-width:780px'>"
        f"<h2 style='margin-bottom:0'>TradePro Daily Digest</h2>"
        f"<div style='color:#666;font-size:12px;margin-bottom:16px'>{today} UTC</div>"
        f"{banner_html}"
        f"{_html_block(buys, f'BUY candidates ({len(buys)})', '#1fc16b')}"
        f"{_html_block(avoids, f'AVOID ({len(avoids)})', '#e2483a')}"
        f"{_html_block(waits, f'WAIT ({len(waits)})', '#e8a23a')}"
        f"<div style='color:#999;font-size:11px;margin-top:24px'>"
        f"Verdicts come from the rule engine + multi-strategy vote; "
        f"every number traces to a structured fact in the API."
        f"</div></div>"
    )

    return EmailDigest(subject=subject, text_body=text_body, html_body=html_body)
