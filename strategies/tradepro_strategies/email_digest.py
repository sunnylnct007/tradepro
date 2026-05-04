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
            items.append({
                "symbol": sym,
                "universe": universe,
                "bucket": best.get("bucket"),
                "bucket_reason": best.get("bucket_reason"),
                "long_count": sum(1 for r in sym_rows if r.get("in_position")),
                "total_strategies": len(sym_rows),
                "strategy": best.get("strategy"),
                "rsi_14": ms.get("rsi_14"),
                "pct_off_52w_high_pct": ms.get("pct_off_52w_high_pct"),
                "drawdown_from_peak_pct": ms.get("drawdown_from_peak_pct"),
                "cagr_pct": stats.get("cagr_pct"),
                "sharpe": stats.get("sharpe"),
                "max_drawdown_pct": stats.get("max_drawdown_pct"),
                "max_drawdown_recovery_days": stats.get("max_drawdown_recovery_days"),
                "max_drawdown_still_recovering": stats.get("max_drawdown_still_recovering"),
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


def _text_block(items: list[dict], heading: str) -> str:
    lines = [heading, "─" * len(heading)]
    if not items:
        lines.append("(none)")
        return "\n".join(lines)
    for it in items:
        sym = it["symbol"]
        univ = it.get("universe") or "—"
        long = it.get("long_count")
        total = it.get("total_strategies")
        consensus = (
            f"{long} of {total} strategies long"
            if long is not None and total
            else "—"
        )
        lines.append(f"{sym}  ·  {univ}  ·  {it['bucket']}  ·  {consensus}")
        if it.get("bucket_reason"):
            lines.append(f"  {it['bucket_reason']}")
        rsi = _fmt(it.get("rsi_14"), digits=0)
        off_high = _fmt(it.get("pct_off_52w_high_pct"), "%", 1)
        cagr = _fmt(it.get("cagr_pct"), "%", 1)
        sharpe = _fmt(it.get("sharpe"), digits=2)
        max_dd = _format_dd_recovery(it)
        lines.append(
            f"  RSI {rsi} · {off_high} off 52w · "
            f"CAGR {cagr} · Sharpe {sharpe} · MaxDD {max_dd}"
        )
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


def build_digest(payloads: list[dict]) -> EmailDigest:
    """Build the digest. `payloads` is a list of compare envelopes —
    one per universe — matching the shape the API returns from
    /api/compare/latest."""
    buys = _filter_bucket(payloads, "BUY")
    avoids = _filter_bucket(payloads, "AVOID")
    waits = _filter_bucket(payloads, "WAIT")
    today = _now_str()

    subject = (
        f"TradePro Digest {today} — {len(buys)} BUY · "
        f"{len(waits)} WAIT · {len(avoids)} AVOID"
    )

    banner = _staleness_banner(payloads)
    header_lines = [f"TradePro Daily Digest — {today}"]
    if banner:
        header_lines.append(banner)
    text_body = "\n\n".join([
        "\n".join(header_lines),
        f"BUY candidates ({len(buys)})",
        _text_block(buys, "BUY") if buys else "(none today)",
        f"AVOID ({len(avoids)})",
        _text_block(avoids, "AVOID") if avoids else "(none today)",
        f"WAIT ({len(waits)})",
        _text_block(waits, "WAIT") if waits else "(none today)",
        "Verdicts come from the rule engine + multi-strategy vote;\n"
        "every number traces to a structured fact in the API.",
    ])

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
