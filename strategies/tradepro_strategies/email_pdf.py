"""Daily-digest PDF attachment.

Produces a single-document PDF mailed alongside the HTML body of
each digest. The HTML body itself remains the at-a-glance read; the
PDF is the deeper drill-down — full decision trace per BUY name,
horizon classification verdicts, holdings advice with narratives,
and a glossary so a newcomer can read the file without an external
explainer.

Design rules:
  * Reuse the exact PNG charts the HTML email embeds (donut /
    holdings P&L bar / sparklines) so the two surfaces match.
  * Each per-symbol page must be self-contained — a reader who
    opens to page 5 should be able to interpret the verdict from
    that page alone.
  * No live API calls — the builder accepts the same payloads list
    the digest builder receives, so a digest job can be reproduced
    deterministically from its inputs.

Loaded lazily by `email_digest.build_digest` so a stripped install
that doesn't have reportlab still produces text + HTML body
without the attachment. The email send path skips the attachment
gracefully when this module's import raises ImportError.
"""
from __future__ import annotations

import base64
import io
from datetime import datetime, timezone
from html import escape as _html_escape
from typing import Any

# Lazy / soft import — reportlab pulled in on first use, not at
# module import. The whole module is itself imported lazily by
# email_digest, so a deployment with no PDF dep produces a clean
# graceful skip rather than crashing the digest send.
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# Brand colours — match email_charts.py + frontend.
_UP = colors.HexColor("#1fc16b")
_DOWN = colors.HexColor("#e2483a")
_NEUTRAL = colors.HexColor("#e8a23a")
_TEXT = colors.HexColor("#222222")
_TEXT_DIM = colors.HexColor("#555555")
_BORDER = colors.HexColor("#dde2e8")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_digest_pdf(
    payloads: list[dict],
    *,
    holdings: list[dict] | None = None,
    portfolio_mode: str | None = None,
) -> bytes:
    """Render the day's digest as a PDF and return raw bytes.

    Inputs match `build_digest`. Returns an empty bytes object only
    when the input is so degenerate (no payloads at all) that there
    is nothing to render — caller can decide to skip the attachment
    in that case."""
    if not payloads:
        return b""

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"TradePro Digest {_today_iso()}",
        author="TradePro",
    )
    flow: list[Any] = []
    styles = _build_styles()

    buys = _filter_bucket(payloads, "BUY")
    waits = _filter_bucket(payloads, "WAIT")
    avoids = _filter_bucket(payloads, "AVOID")

    # ---- Cover ----
    flow.extend(_cover_page(
        styles, buys, waits, avoids,
        holdings=holdings or [], portfolio_mode=portfolio_mode,
    ))
    flow.append(PageBreak())

    # ---- Methodology brief ----
    flow.extend(_methodology_section(styles))
    flow.append(PageBreak())

    # ---- Holdings section ----
    if holdings:
        flow.extend(_holdings_section(styles, holdings, payloads))
        flow.append(PageBreak())

    # ---- Per-symbol detail (BUYs first, then AVOIDs, then WAITs) ----
    flow.extend(_symbol_pages(
        styles,
        buys, "BUY", "Today's BUY candidates", _UP,
    ))
    if avoids:
        flow.extend(_symbol_pages(
            styles, avoids, "AVOID", "Today's AVOIDs", _DOWN,
        ))
    if waits:
        flow.extend(_symbol_pages(
            styles, waits, "WAIT", "Today's WAITs", _NEUTRAL,
        ))

    # ---- Glossary ----
    flow.append(PageBreak())
    flow.extend(_glossary_section(styles))

    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Style sheet — single source of truth for fonts / sizes
# ---------------------------------------------------------------------------


def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"],
            fontSize=22, leading=26, textColor=_TEXT, alignment=0,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"],
            fontSize=14, leading=18, spaceAfter=4, textColor=_TEXT,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"],
            fontSize=11, leading=14, spaceBefore=8, spaceAfter=2,
            textColor=_TEXT,
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"],
            fontSize=9.5, leading=13, textColor=_TEXT,
        ),
        "dim": ParagraphStyle(
            "dim", parent=base["BodyText"],
            fontSize=9, leading=12, textColor=_TEXT_DIM,
        ),
        "small": ParagraphStyle(
            "small", parent=base["BodyText"],
            fontSize=8, leading=11, textColor=_TEXT_DIM,
        ),
        "verdict": ParagraphStyle(
            "verdict", parent=base["Heading3"],
            fontSize=12, leading=15, spaceAfter=6,
            textColor=_TEXT,
        ),
    }


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------


def _cover_page(
    styles, buys, waits, avoids, *,
    holdings: list[dict], portfolio_mode: str | None,
) -> list[Any]:
    flow: list[Any] = []
    flow.append(Paragraph(
        f"TradePro Daily Digest", styles["title"],
    ))
    flow.append(Paragraph(
        f"<font color='#666'>{_today_iso()} UTC · "
        f"{len(buys)} BUY · {len(waits)} WAIT · {len(avoids)} AVOID"
        + (f" · T212 {portfolio_mode.upper()}" if portfolio_mode else "")
        + "</font>",
        styles["dim"],
    ))
    flow.append(Spacer(1, 10))

    # Bucket donut + headline summary side-by-side.
    donut_png = _maybe_png(_make_donut, len(buys), len(waits), len(avoids))
    if donut_png is not None:
        flow.append(Image(donut_png, width=100 * mm, height=58 * mm))

    # Decision-readiness banner — staleness / portfolio coverage.
    flow.append(Spacer(1, 8))
    flow.append(Paragraph(
        "<b>What's in this PDF</b>", styles["h3"],
    ))
    bullets = [
        ("Methodology & rules", "the rule chain that produced today's verdicts"),
        ("Your portfolio", "every T212 position with horizon-aware advice")
            if holdings else None,
        ("Per-symbol pages", "decision trace + horizon scores + sentiment + analyst targets"),
        ("Glossary", "every metric the engine uses, in plain English"),
    ]
    for entry in bullets:
        if entry is None:
            continue
        title, body = entry
        flow.append(Paragraph(
            f"&bull; <b>{_escape(title)}</b> — {_escape(body)}",
            styles["body"],
        ))
    flow.append(Spacer(1, 10))

    # Top-3 BUYs preview — one-line each so the cover already
    # answers "what should I look at?" without the user opening the
    # full per-symbol pages.
    if buys:
        flow.append(Paragraph(
            "<b>Top BUYs at a glance</b>", styles["h3"],
        ))
        for it in buys[:3]:
            sym = it.get("symbol") or "?"
            label = it.get("label") or ""
            flow.append(Paragraph(
                f"<b>{_escape(sym)}</b> &nbsp; "
                f"<font color='#666'>{_escape(label)}</font>",
                styles["body"],
            ))

    return flow


# ---------------------------------------------------------------------------
# Methodology section
# ---------------------------------------------------------------------------


def _methodology_section(styles) -> list[Any]:
    flow: list[Any] = [Paragraph("How TradePro decides", styles["h2"])]
    blurb = [
        (
            "<b>Five strategies vote.</b> Each universe is run through SMA "
            "crossover, RSI mean-reversion, MACD signal, Donchian breakout "
            "and buy-and-hold. Strategies are <i>orthogonal</i> on purpose: "
            "they catch different regimes, and disagreement is itself a signal."
        ),
        (
            "<b>Per-symbol market state.</b> A rule-based check (above SMA200, "
            "RSI band, distance from 52w high, drawdown from running peak, "
            "12m momentum, range position within 52w high/low) produces a "
            "BUY / WAIT / AVOID verdict — every check appears in the "
            "decision trace so you can see exactly which rule fired."
        ),
        (
            "<b>Bucket vote.</b> The price verdict is combined with the "
            "strategy consensus (\u2265 majority long → BUY-confirmed)."
        ),
        (
            "<b>Sentiment demotion.</b> Local LLM (llama3.1:8b via Ollama) "
            "scores recent headlines per symbol. 7-day mean \u2264 \u20130.30 with "
            "\u2265 2 material-negatives demotes BUY → WAIT; \u2264 \u20130.45 with \u2265 3 "
            "material-negatives demotes any bucket → AVOID."
        ),
        (
            "<b>Swing composite (0\u20138).</b> Quality (Sharpe + max-DD recovery), "
            "Valuation (basket-relative P/E, yield fallback for ETFs), "
            "Event (earnings beat-and-retreat), and Price (consensus + RSI/SMA). "
            "\u22656 = STRONG_BUY, 4\u20135 = BUY, 2\u20133 = HOLD, 0\u20131 = AVOID."
        ),
        (
            "<b>Horizon classification (NEW).</b> Three independent verdicts "
            "per symbol — swing (1\u20138 weeks), long-term (6\u201318 months), "
            "passive (3\u20135 years). Same instrument can simultaneously be a "
            "poor swing entry and an excellent passive vehicle. Single-stock "
            "names return N/A on passive (use the long-term horizon)."
        ),
        (
            "<b>What this PDF is not.</b> This is a decision aid, not advice. "
            "All numbers come from public Yahoo Finance + Finnhub data. The "
            "engine never overrides your judgement and never places a trade."
        ),
    ]
    for para in blurb:
        flow.append(Paragraph(para, styles["body"]))
        flow.append(Spacer(1, 4))
    return flow


# ---------------------------------------------------------------------------
# Holdings section
# ---------------------------------------------------------------------------


def _holdings_section(styles, holdings, payloads) -> list[Any]:
    from .holdings import analyse_holding

    flow: list[Any] = [Paragraph("Your portfolio (T212)", styles["h2"])]
    flow.append(Paragraph(
        "Each position cross-referenced against today's verdict. The ACTION "
        "column comes from the same horizon-aware engine the email digest "
        "and dashboard use, so all three surfaces hand out identical advice.",
        styles["dim"],
    ))
    flow.append(Spacer(1, 6))

    # Embed the P&L bar chart so the reader sees relative position
    # health at a glance before drilling into the table below.
    pnl_png = _maybe_png(_make_holdings_pnl_bar, holdings)
    if pnl_png is not None:
        flow.append(Image(pnl_png, width=170 * mm, height=80 * mm))
        flow.append(Spacer(1, 6))

    # Tabular summary — one row per position. Sorted by action
    # priority (TRIM → BUY_MORE → HOLD) to match the dashboard.
    priority = {"TRIM": 0, "BUY_MORE": 1, "HOLD": 2}
    rows_data = []
    for h in holdings:
        sym = h.get("yahooSymbol") or h.get("ticker") or ""
        row = _row_for_symbol(sym, payloads) if sym else None
        rec = analyse_holding(h, row)
        rows_data.append((rec, h, row))
    rows_data.sort(
        key=lambda x: (
            priority.get(x[0].action, 9),
            -abs(float(x[1].get("unrealisedPct") or 0.0)),
        ),
    )

    table_data = [["Holding", "Qty / Avg", "P&L %", "Today", "Action"]]
    for rec, h, _row in rows_data:
        sym = (h.get("yahooSymbol") or h.get("ticker") or "?").upper()
        name = (h.get("instrumentName") or sym)[:26]
        qty = h.get("quantity") or 0
        avg = h.get("averagePricePaid")
        ccy = h.get("currency") or ""
        pct = h.get("unrealisedPct")
        bucket = (rec_bucket := _row_bucket(payloads, sym)) or "—"
        table_data.append([
            f"{name}\n{sym}",
            f"{qty:.4f}\n@ {avg:.2f} {ccy}" if avg is not None else f"{qty:.4f}",
            f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "—",
            bucket,
            rec.action.replace("_", " "),
        ])
    tbl = Table(table_data, colWidths=[55 * mm, 30 * mm, 22 * mm, 22 * mm, 30 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f6f8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), _TEXT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, _BORDER),
    ]))
    flow.append(tbl)
    flow.append(Spacer(1, 8))

    # Per-position narrative — the actual prose advice.
    flow.append(Paragraph("<b>Per-position advice</b>", styles["h3"]))
    for rec, h, _row in rows_data:
        sym = (h.get("yahooSymbol") or h.get("ticker") or "?").upper()
        flow.append(Paragraph(
            f"<b>{_escape(sym)}</b> &mdash; {_escape(rec.action.replace('_', ' '))}",
            styles["body"],
        ))
        flow.append(Paragraph(_escape(rec.narrative), styles["dim"]))
        if rec.evidence:
            flow.append(Paragraph(
                "<i>" + _escape(" · ".join(rec.evidence)) + "</i>",
                styles["small"],
            ))
        flow.append(Spacer(1, 4))

    return flow


# ---------------------------------------------------------------------------
# Per-symbol pages
# ---------------------------------------------------------------------------


def _symbol_pages(styles, items, bucket_label, section_title, accent) -> list[Any]:
    flow: list[Any] = [Paragraph(section_title, styles["h2"])]
    flow.append(Paragraph(
        f"<font color='#666'>{len(items)} symbol(s) in this section. Each "
        f"page is self-contained — bucket reason, full decision trace, "
        f"horizon classification, sentiment summary and analyst target.</font>",
        styles["dim"],
    ))
    flow.append(Spacer(1, 4))

    for it in items:
        sym = it.get("symbol") or "?"
        label = it.get("label") or ""
        page_blocks: list[Any] = []
        # reportlab's <font color="..."> needs the #-prefixed form;
        # `accent.hexval()` returns "0x1fc16b" so strip + re-prefix.
        accent_css = "#" + accent.hexval()[2:]
        page_blocks.append(Paragraph(
            f"<font color='{accent_css}'>{_escape(bucket_label)}</font> &nbsp; "
            f"<b>{_escape(sym)}</b> &nbsp; "
            f"<font color='#666'>{_escape(label)}</font>",
            styles["verdict"],
        ))
        if it.get("bucket_reason"):
            page_blocks.append(Paragraph(
                _escape(it["bucket_reason"]), styles["body"],
            ))
            page_blocks.append(Spacer(1, 4))

        # Decision trace — a structured table the reader can scan.
        ms = it.get("market_state") or {}
        trace = ms.get("decision_trace") or []
        if trace:
            page_blocks.append(Paragraph(
                "<b>Decision trace</b>", styles["h3"],
            ))
            page_blocks.append(_trace_table(trace))
            page_blocks.append(Spacer(1, 4))

        # Swing composite breakdown.
        sw = it.get("swing_score")
        if sw and sw.get("total") is not None:
            page_blocks.append(Paragraph(
                f"<b>Swing composite:</b> {sw.get('total')}/8 — "
                f"{_escape(str(sw.get('verdict') or ''))}",
                styles["h3"],
            ))
            layers = sw.get("layers") or {}
            reasons = sw.get("reasons") or {}
            for k in ("quality", "valuation", "event", "price"):
                page_blocks.append(Paragraph(
                    f"<b>{k.title()}:</b> {layers.get(k, 0)}/2 — "
                    f"{_escape(str(reasons.get(k) or '—'))}",
                    styles["body"],
                ))
            page_blocks.append(Spacer(1, 4))

        # Horizon classification — three pills, side by side.
        hz = it.get("horizon_classification") or {}
        if hz:
            page_blocks.append(Paragraph(
                "<b>Horizon classification</b>", styles["h3"],
            ))
            page_blocks.append(_horizon_table(hz))
            if hz.get("range_pct") is not None:
                page_blocks.append(Paragraph(
                    f"<font color='#555'>Range position: "
                    f"{hz['range_pct']:.0f}th percentile of 52w range</font>",
                    styles["small"],
                ))
            page_blocks.append(Spacer(1, 4))

        # Sentiment summary.
        ss = it.get("sentiment_summary") or {}
        if ss.get("mean_sentiment") is not None:
            page_blocks.append(Paragraph(
                "<b>Sentiment (7d)</b>", styles["h3"],
            ))
            page_blocks.append(Paragraph(
                f"Mean: {ss.get('mean_sentiment'):+.2f} &nbsp;|&nbsp; "
                f"Material negatives: {ss.get('material_negative_count', 0)} &nbsp;|&nbsp; "
                f"Headlines scored: {ss.get('headlines_scored', 0)}",
                styles["body"],
            ))
            if it.get("sentiment_demoted"):
                page_blocks.append(Paragraph(
                    "<font color='#e8a23a'><b>⚠ Demoted by sentiment rule.</b></font>",
                    styles["body"],
                ))
            page_blocks.append(Spacer(1, 4))

        # Analyst consensus snapshot (when present).
        ext = it.get("external_consensus") or {}
        if ext.get("target_mean"):
            up = ((ext.get("target_mean", 0) - ext.get("price_at_snapshot", 0))
                  / ext.get("price_at_snapshot", 1) * 100.0
                  if ext.get("price_at_snapshot") else None)
            page_blocks.append(Paragraph(
                "<b>Analyst consensus</b>", styles["h3"],
            ))
            page_blocks.append(Paragraph(
                f"Target mean: <b>{ext.get('target_mean'):.2f}</b> &nbsp;|&nbsp; "
                f"Analysts: {ext.get('analyst_count', '?')}"
                + (f" &nbsp;|&nbsp; Implied upside: {up:+.1f}%" if up is not None else ""),
                styles["body"],
            ))

        # Wrap each symbol in KeepTogether so it doesn't split mid-
        # paragraph; reportlab will overflow to a new page when the
        # block exceeds a single page.
        flow.append(KeepTogether(page_blocks))
        flow.append(Spacer(1, 10))

    return flow


def _trace_table(trace: list[dict]) -> Table:
    rows = [["Check", "Status", "Detail"]]
    for r in trace:
        rows.append([
            r.get("name", ""),
            (r.get("status") or "").upper(),
            r.get("detail", ""),
        ])
    tbl = Table(rows, colWidths=[55 * mm, 20 * mm, 90 * mm])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f6f8")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("GRID", (0, 0), (-1, -1), 0.3, _BORDER),
    ]
    # Colour the status cells per check.
    status_colour = {"pass": _UP, "fail": _DOWN, "warn": _NEUTRAL}
    for i, r in enumerate(trace, start=1):
        c = status_colour.get((r.get("status") or "").lower())
        if c is not None:
            style.append(("TEXTCOLOR", (1, i), (1, i), c))
            style.append(("FONTNAME", (1, i), (1, i), "Helvetica-Bold"))
    tbl.setStyle(TableStyle(style))
    return tbl


def _horizon_table(hz: dict) -> Table:
    """Three columns: Swing | Long-term | Passive. Each cell shows
    signal, score, top reasons, optional entry note. Colour-codes
    the signal pill row so the reader's eye lands on it first."""
    sw = hz.get("swing") or {}
    lt = hz.get("long_term") or {}
    pa = hz.get("passive") or {}

    def _cell(v: dict) -> str:
        sig = (v.get("signal") or "?").upper()
        score = v.get("score") or "?"
        reasons = v.get("reasons") or []
        body = "<br/>".join(f"&bull; {_html_escape(r)}" for r in reasons[:3])
        note = v.get("entry_note")
        out = f"<b>{_html_escape(sig)}</b> &nbsp; <i>{_html_escape(score)}</i><br/><br/>{body}"
        if note:
            out += f"<br/><br/><i>{_html_escape(str(note))}</i>"
        return out

    base = getSampleStyleSheet()["BodyText"]
    cell_style = ParagraphStyle("hz_cell", parent=base, fontSize=8.5,
                                leading=11, textColor=_TEXT)
    rows = [
        [
            Paragraph("<b>Swing</b><br/>1-8 weeks", cell_style),
            Paragraph("<b>Long-term</b><br/>6-18 months", cell_style),
            Paragraph("<b>Passive</b><br/>3-5 years", cell_style),
        ],
        [
            Paragraph(_cell(sw), cell_style),
            Paragraph(_cell(lt), cell_style),
            Paragraph(_cell(pa), cell_style),
        ],
    ]
    tbl = Table(rows, colWidths=[55 * mm, 55 * mm, 55 * mm])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f6f8")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, _BORDER),
    ]
    tbl.setStyle(TableStyle(style))
    return tbl


# ---------------------------------------------------------------------------
# Glossary
# ---------------------------------------------------------------------------


def _glossary_section(styles) -> list[Any]:
    rows = [
        ("BUY / WAIT / AVOID",
         "Per-symbol bucket. BUY = price action + ≥ majority of strategies long. "
         "WAIT = stretched / mid-drawdown / sentiment-demoted. AVOID = confirmed "
         "downtrend or hostile news flow."),
        ("Swing score (0-8)",
         "Composite across Quality, Valuation, Event, Price layers. ≥6 STRONG_BUY, "
         "4-5 BUY, 2-3 HOLD, 0-1 AVOID."),
        ("Horizon classification",
         "Three independent verdicts: Swing (1-8w), Long-term (6-18mo), Passive "
         "(3-5y). Same symbol can score differently per horizon."),
        ("Range position (52w)",
         "Where the current price sits as a percentile within the 52w (low → high) "
         "range. ≥70th = near the highs (limited swing upside). ≤40th = near the "
         "lows (genuine dip). Hard cap at WATCH on swing for ≥80th pctile."),
        ("RSI (14-day)",
         "Momentum oscillator. <30 oversold, >70 overbought. Mid-range is healthy."),
        ("SMA200",
         "200-day simple moving average. Above = uptrend. Below = downtrend."),
        ("Drawdown from peak (5y)",
         "Distance from the highest price in the full series. Long-term valuation "
         "signal — NOT a short-term entry trigger."),
        ("Bucket consensus",
         "How many of the 5 strategies are currently long. Majority = BUY-confirmed."),
        ("Sentiment demotion",
         "7d mean ≤ -0.30 + ≥2 material-negative headlines → BUY → WAIT. "
         "≤ -0.45 + ≥3 material → any → AVOID."),
        ("Valuation flag",
         "Cross-sectional vs basket peers. P/E quartile for stocks (lower = cheaper); "
         "yield quartile fallback for ETFs (higher = cheaper)."),
        ("Earnings beat-and-retreat",
         "Stock beat estimates AND has retreated 5-15% from post-earnings peak "
         "within a 60-day window. Classic event-driven swing entry."),
    ]
    flow: list[Any] = [Paragraph("Glossary", styles["h2"])]
    for term, definition in rows:
        flow.append(Paragraph(
            f"<b>{_escape(term)}</b>", styles["body"],
        ))
        flow.append(Paragraph(_escape(definition), styles["dim"]))
        flow.append(Spacer(1, 3))
    return flow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _rows_of(p: dict) -> list[dict]:
    """Compare-payload envelope shape varies — the api response has
    rows under `payload.rows` while a direct comparator emit has
    `rows` at top level. Accept both so the PDF doesn't silently
    render an empty BUY section because of an envelope mismatch
    (the bug from 2026-05-09: email said 29 BUY, PDF said 0)."""
    if not p:
        return []
    inner = p.get("payload")
    if isinstance(inner, dict) and inner.get("rows"):
        return inner["rows"]
    return p.get("rows") or []


def _filter_bucket(payloads: list[dict], bucket: str) -> list[dict]:
    """Best-rank row per symbol where bucket matches. Same logic as
    email_digest._filter_bucket — accepts both envelope shapes."""
    seen: dict[str, dict] = {}
    for p in payloads or []:
        for r in _rows_of(p):
            if (r.get("bucket") or "").upper() != bucket:
                continue
            sym = r.get("symbol") or ""
            if not sym:
                continue
            existing = seen.get(sym)
            if not existing or (r.get("rank") or 1e9) < (existing.get("rank") or 1e9):
                seen[sym] = r
    return sorted(seen.values(), key=lambda r: r.get("rank") or 1e9)


def _row_for_symbol(sym: str, payloads: list[dict]) -> dict | None:
    sym_u = (sym or "").upper()
    best: dict | None = None
    best_rank = 1e9
    for p in payloads or []:
        for r in _rows_of(p):
            if (r.get("symbol") or "").upper() != sym_u:
                continue
            rank = r.get("rank") or 1e9
            if rank < best_rank:
                best_rank = rank
                best = r
    return best


def _row_bucket(payloads: list[dict], sym: str) -> str | None:
    r = _row_for_symbol(sym, payloads)
    return r.get("bucket") if r else None


def _maybe_png(fn, *args, **kwargs) -> io.BytesIO | None:
    """Call a chart fn that returns a base64 data URL. Decode and
    return BytesIO so reportlab can embed it. None on any failure
    so the PDF skips the chart instead of crashing."""
    try:
        url = fn(*args, **kwargs)
        if not url or not url.startswith("data:image/png;base64,"):
            return None
        encoded = url.split(",", 1)[1]
        buf = io.BytesIO(base64.b64decode(encoded))
        buf.seek(0)
        return buf
    except Exception:  # noqa: BLE001
        return None


def _make_donut(b: int, w: int, a: int):
    from .email_charts import bucket_donut_png
    return bucket_donut_png(b, w, a)


def _make_holdings_pnl_bar(holdings):
    from .email_charts import holdings_pnl_bar_png
    return holdings_pnl_bar_png(holdings)


def _escape(s: Any) -> str:
    """ReportLab Paragraph parses a tiny HTML subset. Escape angle
    brackets / ampersands so user-supplied strings (basket reasons,
    instrument names) don't break the parser."""
    return _html_escape(str(s) if s is not None else "")


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(_TEXT_DIM)
    canvas.drawString(
        18 * mm, 10 * mm,
        f"TradePro Daily Digest · {_today_iso()} UTC · "
        f"Decision aid, not advice. Numbers source: Yahoo Finance + Finnhub.",
    )
    canvas.drawRightString(
        doc.pagesize[0] - 18 * mm, 10 * mm,
        f"Page {canvas.getPageNumber()}",
    )
    canvas.restoreState()
