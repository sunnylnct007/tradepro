"""AWS SES email composition and delivery (SRS Section 12)."""
from __future__ import annotations

import base64
import datetime
import logging

import boto3

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
    from reportlab.lib import colors
    import io
    _PDF_ENABLED = True
except Exception:
    _PDF_ENABLED = False

try:
    from chart_generator import generate_chart_b64
    _CHARTS_ENABLED = True
except Exception:
    _CHARTS_ENABLED = False

try:
    from analysis_generator import wheel_analysis, swing_analysis
    _ANALYSIS_ENABLED = True
except Exception:
    _ANALYSIS_ENABLED = False

log = logging.getLogger("screener.email")

TO_ADDRESS = "info@coreconsultingit.com"
FROM_ADDRESS = "info@coreconsultingit.com"  # must be SES-verified
AWS_REGION = "eu-west-2"


def _ses_client():
    return boto3.client("ses", region_name=AWS_REGION)


# ------------------------------------------------------------------
# Wheel email
# ------------------------------------------------------------------

def send_wheel_email(candidates: list, run_date: datetime.date | None = None) -> bool:
    date = run_date or datetime.date.today()
    date_str = date.strftime("%d %b %Y")
    n = len(candidates)

    subject = f"🎡 TradePro Wheel — {n} candidates — {date_str}"
    body = _build_wheel_html(candidates, date_str)
    pdf_bytes = _build_pdf(candidates, "wheel", date_str) if _PDF_ENABLED else None
    return _send(subject, body, pdf_bytes=pdf_bytes, pdf_name=f"wheel_{date.isoformat()}.pdf")


def _build_wheel_html(candidates: list, date_str: str) -> str:
    if not candidates:
        return _no_candidates_html(
            "wheel", "No wheel candidates today — all failed gate or scored < 5/14", date_str
        )

    rows = ""
    for c in candidates:
        dual = "<div class='dual'>⚡ DUAL CANDIDATE</div>" if c.dual_candidate else ""
        chart_html = ""
        if _CHARTS_ENABLED and hasattr(c, "_bars") and c._bars:
            b64 = generate_chart_b64(c._bars, c.ticker)
            if b64:
                chart_html = f"<img src='data:image/png;base64,{b64}' style='width:100%;border-radius:4px;margin:8px 0;' alt='{c.ticker} chart'/>"
        analysis = wheel_analysis(c) if _ANALYSIS_ENABLED else c.explanation
        # support / resistance from bars
        sr_html = _sr_html(c)
        rows += f"""
        <div class='card'>
          {dual}
          <div class='header'>{c.ticker} — ${c.price:.2f}
            <span class='vol-label {c.volatility_label.lower()}'>{c.volatility_label}</span>
          </div>
          <table>
            <tr><td>Score</td><td><strong>{c.score}/14</strong></td>
                <td>IV Rank</td><td><strong>{c.ivr:.0f}%</strong></td>
                <td>Dividend</td><td><strong>{c.div_yield:.1f}%</strong></td></tr>
            <tr><td>HV Annual</td><td>{getattr(c,'hv_annual',0):.1f}%</td>
                <td>Premium est.</td><td>{c.premium_pct:.1f}%/mo</td>
                <td>52w low dist</td><td>{c.dist_52w_low_pct:.1f}%</td></tr>
            <tr><td>Trend</td><td colspan='5'>{c.trend_status}</td></tr>
          </table>
          {sr_html}
          {chart_html}
          {_option_chain_html(c)}
          <div class='suggest'>📌 Suggested: Sell ${c.suggested_strike:.0f} Put expiring {c.suggested_expiry}</div>
          <div class='guidance'>⛔ Close guidance: {c.close_guidance}</div>
          <div class='claude'><strong>Analysis:</strong> {analysis}</div>
        </div>"""

    return _wrap_html(f"🎡 TradePro Wheel — {date_str}", rows)


# ------------------------------------------------------------------
# Swing email
# ------------------------------------------------------------------

def send_swing_email(candidates: list, run_date: datetime.date | None = None) -> bool:
    date = run_date or datetime.date.today()
    date_str = date.strftime("%d %b %Y")
    n = len(candidates)

    subject = f"📈 TradePro Swing — {n} candidates — {date_str}"
    body = _build_swing_html(candidates, date_str)
    pdf_bytes = _build_pdf(candidates, "swing", date_str) if _PDF_ENABLED else None
    return _send(subject, body, pdf_bytes=pdf_bytes, pdf_name=f"swing_{date.isoformat()}.pdf")


def _build_swing_html(candidates: list, date_str: str) -> str:
    if not candidates:
        return _no_candidates_html(
            "swing", "No swing candidates today — all failed gate or scored < 5/14", date_str
        )

    rows = ""
    for c in candidates:
        dual = "<div class='dual'>⚡ DUAL CANDIDATE</div>" if c.dual_candidate else ""
        chart_html = ""
        if _CHARTS_ENABLED and hasattr(c, "_bars") and c._bars:
            b64 = generate_chart_b64(c._bars, c.ticker)
            if b64:
                chart_html = f"<img src='data:image/png;base64,{b64}' style='width:100%;border-radius:4px;margin:8px 0;' alt='{c.ticker} chart'/>"
        analysis = swing_analysis(c) if _ANALYSIS_ENABLED else c.explanation
        sr_html = _sr_html(c)
        rows += f"""
        <div class='card'>
          {dual}
          <div class='header'>{c.ticker} — ${c.price:.2f}</div>
          <table>
            <tr><td>Score</td><td><strong>{c.score}/14</strong></td>
                <td>RSI(14)</td><td><strong>{c.rsi_val:.1f}</strong></td>
                <td>vs 20d MA</td><td><strong>{c.ma20_dist_pct:+.1f}%</strong></td></tr>
            <tr><td>Volume</td><td colspan='2'>{c.vol_signal}</td>
                <td>vs SPY (20d)</td><td colspan='2'>{c.rs_status}</td></tr>
          </table>
          <div class='trend'>📊 Trend: {c.trend_status}</div>
          {sr_html}
          {chart_html}
          <div class='claude'><strong>Analysis:</strong> {analysis}</div>
        </div>"""

    return _wrap_html(f"📈 TradePro Swing — {date_str}", rows)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _option_chain_html(c) -> str:
    """Build real option chain table from WheelCandidate option fields."""
    chain = getattr(c, "option_chain", [])
    atm_strike = getattr(c, "atm_put_bid", None)
    if not chain and not getattr(c, "atm_put_mid", 0):
        return ""
    # Build rows: ATM put summary + chain
    rows_html = ""
    if getattr(c, "atm_put_mid", 0):
        iv = getattr(c, "atm_put_iv_pct", 0)
        oi = getattr(c, "atm_put_oi", 0)
        rows_html += f"""<tr style='background:#e8f4fd'>
          <td><strong>${c.suggested_strike:.1f} ATM</strong></td>
          <td>${c.atm_put_bid:.2f}</td><td>${c.atm_put_ask:.2f}</td>
          <td><strong>${c.atm_put_mid:.2f}</strong></td>
          <td>{iv:.1f}%</td><td>{oi:,}</td></tr>"""
    for row in chain:
        strike = row.get("strike", 0)
        bid = row.get("bid", 0) or 0
        ask = row.get("ask", 0) or 0
        mid = round((bid + ask) / 2, 2) if bid and ask else 0
        iv_r = row.get("iv_pct", 0) or 0
        oi_r = row.get("oi", 0) or 0
        is_atm = abs(strike - getattr(c, "suggested_strike", 0)) < 0.01
        style = " style='background:#e8f4fd'" if is_atm else ""
        rows_html += f"""<tr{style}>
          <td>${strike:.1f}</td>
          <td>${bid:.2f}</td><td>${ask:.2f}</td><td>${mid:.2f}</td>
          <td>{iv_r:.1f}%</td><td>{oi_r:,}</td></tr>"""
    if not rows_html:
        return ""
    expiry = getattr(c, "option_expiry", "") or getattr(c, "suggested_expiry", "")
    return f"""
    <div class='optchain'>
      <strong>Option Chain — Puts expiring {expiry}</strong>
      <table style='width:100%;font-size:12px;border-collapse:collapse;margin-top:4px'>
        <tr style='background:#ddd;font-weight:bold'>
          <td>Strike</td><td>Bid</td><td>Ask</td><td>Mid</td><td>IV</td><td>OI</td></tr>
        {rows_html}
      </table>
    </div>"""


def _sr_html(c) -> str:
    """Build support/resistance row from candidate bars if available."""
    if not hasattr(c, "_bars") or not c._bars:
        return ""
    bars = c._bars
    try:
        highs = [float(b["h"]) for b in bars if b.get("h")]
        lows  = [float(b["l"]) for b in bars if b.get("l")]
        closes = [float(b["c"]) for b in bars if b.get("c")]
        if len(highs) < 20:
            return ""
        lookback = min(60, len(highs))
        resistance = max(highs[-lookback:])
        support    = min(lows[-lookback:])
        ma20 = sum(closes[-20:]) / 20
        ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
        ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
        ma_str = f"MA20 ${ma20:.2f}"
        if ma50:  ma_str += f" · MA50 ${ma50:.2f}"
        if ma200: ma_str += f" · MA200 ${ma200:.2f}"
        return f"""
        <div class='srbox'>
          <span class='resist'>⬆ Resistance: ${resistance:.2f}</span>
          &nbsp;&nbsp;
          <span class='supp'>⬇ Support: ${support:.2f}</span>
          <br/><span class='maline'>{ma_str}</span>
        </div>"""
    except Exception:
        return ""


def _no_candidates_html(kind: str, msg: str, date_str: str) -> str:
    return _wrap_html(
        f"TradePro {kind.title()} — {date_str}",
        f"<p style='color:#888'>{msg}</p>",
    )


def _wrap_html(title: str, body_content: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'>
<style>
  body {{ font-family: Arial, sans-serif; font-size: 14px; color: #222; max-width: 700px; margin: 0 auto; padding: 20px; }}
  h1 {{ font-size: 20px; color: #333; border-bottom: 2px solid #eee; padding-bottom: 8px; }}
  .card {{ border: 1px solid #ddd; border-radius: 6px; padding: 14px; margin-bottom: 16px; background: #fafafa; }}
  .header {{ font-size: 17px; font-weight: bold; margin-bottom: 8px; }}
  .dual {{ background: #fff3cd; color: #856404; padding: 4px 8px; border-radius: 4px; font-weight: bold; margin-bottom: 6px; display: inline-block; }}
  table {{ width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 13px; }}
  td {{ padding: 3px 6px; }}
  td:nth-child(odd) {{ color: #666; }}
  .suggest {{ background: #e8f4fd; padding: 6px 10px; border-radius: 4px; margin: 6px 0; }}
  .guidance {{ font-size: 12px; color: #555; margin: 4px 0; }}
  .trend {{ font-size: 12px; color: #555; margin: 4px 0; }}
  .claude {{ background: #f0f7ee; padding: 8px 10px; border-radius: 4px; margin-top: 8px; font-size: 13px; }}
  .vol-label {{ font-size: 11px; padding: 2px 6px; border-radius: 3px; margin-left: 6px; }}
  .conservative {{ background: #d4edda; color: #155724; }}
  .medium {{ background: #fff3cd; color: #856404; }}
  .volatile {{ background: #f8d7da; color: #721c24; }}
  .srbox {{ background: #f8f9fa; border-left: 3px solid #6c757d; padding: 6px 10px; margin: 6px 0; font-size: 12px; }}
  .resist {{ color: #c0392b; font-weight: bold; }}
  .supp {{ color: #27ae60; font-weight: bold; }}
  .maline {{ color: #555; font-size: 11px; }}
  .optchain {{ background: #f9f9f9; border: 1px solid #ccc; border-radius: 4px; padding: 8px; margin: 6px 0; }}
  .optchain table td {{ padding: 2px 6px; border-bottom: 1px solid #eee; }}
</style>
</head><body>
<h1>{title}</h1>
{body_content}
<p style='font-size:11px;color:#aaa;margin-top:24px'>TradePro Screener · Automated · Not financial advice</p>
</body></html>"""


def _build_pdf(candidates: list, kind: str, date_str: str) -> bytes | None:
    """Build a PDF report using ReportLab and return raw bytes, or None on failure."""
    if not _PDF_ENABLED or not candidates:
        return None
    try:
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=1.5*cm, rightMargin=1.5*cm,
                                topMargin=2*cm, bottomMargin=2*cm)
        styles = getSampleStyleSheet()
        story = []
        title_style = styles["Title"]
        h2 = styles["Heading2"]
        normal = styles["Normal"]

        story.append(Paragraph(f"TradePro {kind.title()} Screener — {date_str}", title_style))
        story.append(Spacer(1, 0.4*cm))

        for c in candidates:
            dual = " ⚡ DUAL CANDIDATE" if c.dual_candidate else ""
            story.append(Paragraph(f"{c.ticker} — ${c.price:.2f}{dual}", h2))

            if kind == "wheel":
                tbl_data = [
                    ["Score", f"{c.score}/14", "IV Rank", f"{c.ivr:.0f}%", "Dividend", f"{c.div_yield:.1f}%"],
                    ["HV Annual", f"{getattr(c,'hv_annual',0):.1f}%", "Premium est.", f"{c.premium_pct:.1f}%/mo", "52w low dist", f"{c.dist_52w_low_pct:.1f}%"],
                    ["Trend", c.trend_status, "", "", "", ""],
                    ["Volatility", c.volatility_label, "Close guidance", c.close_guidance, "", ""],
                    ["Suggested", f"Sell ${c.suggested_strike:.0f} Put — {c.suggested_expiry}", "", "", "", ""],
                ]
                if getattr(c, "atm_put_mid", 0):
                    tbl_data.append(["ATM Put", f"Bid ${c.atm_put_bid:.2f} / Ask ${c.atm_put_ask:.2f} / Mid ${c.atm_put_mid:.2f}", "OI", f"{c.atm_put_oi:,}", "IV", f"{c.atm_put_iv_pct:.1f}%"])
            else:
                tbl_data = [
                    ["Score", f"{c.score}/14", "RSI(14)", f"{c.rsi_val:.1f}", "vs 20d MA", f"{c.ma20_dist_pct:+.1f}%"],
                    ["Volume", c.vol_signal, "vs SPY (20d)", c.rs_status, "", ""],
                    ["Trend", c.trend_status, "", "", "", ""],
                ]

            tbl = Table(tbl_data, colWidths=[3*cm, 4*cm, 3*cm, 3*cm, 2.5*cm, 2.5*cm])
            tbl.setStyle(TableStyle([
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
                ("TEXTCOLOR", (2, 0), (2, -1), colors.grey),
                ("TEXTCOLOR", (4, 0), (4, -1), colors.grey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.93, 0.95, 1)),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.Color(0.97, 0.97, 0.97)]),
            ]))
            story.append(tbl)

            # Embed chart if available
            if hasattr(c, "_bars") and c._bars and _CHARTS_ENABLED:
                try:
                    b64 = generate_chart_b64(c._bars, c.ticker)
                    if b64:
                        img_bytes = io.BytesIO(base64.b64decode(b64))
                        img = RLImage(img_bytes, width=16*cm, height=8*cm)
                        story.append(Spacer(1, 0.2*cm))
                        story.append(img)
                except Exception:
                    pass

            # Option chain table
            chain = getattr(c, "option_chain", [])
            if chain:
                story.append(Spacer(1, 0.2*cm))
                story.append(Paragraph(f"Option Chain — Puts expiring {getattr(c,'option_expiry','')}:", normal))
                chain_data = [["Strike", "Bid", "Ask", "Mid", "IV%", "OI"]]
                for row in chain:
                    bid = row.get("bid", 0) or 0
                    ask = row.get("ask", 0) or 0
                    mid = round((bid + ask) / 2, 2) if bid and ask else 0
                    chain_data.append([
                        f"${row.get('strike',0):.1f}",
                        f"${bid:.2f}", f"${ask:.2f}", f"${mid:.2f}",
                        f"{row.get('iv_pct',0):.1f}%",
                        f"{row.get('oi',0):,}",
                    ])
                ct = Table(chain_data, colWidths=[3*cm, 2.5*cm, 2.5*cm, 2.5*cm, 2.5*cm, 3*cm])
                ct.setStyle(TableStyle([
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.8, 0.85, 0.9)),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.Color(0.95, 0.97, 1)]),
                ]))
                story.append(ct)

            analysis = getattr(c, "explanation", "See metrics above.")
            story.append(Spacer(1, 0.2*cm))
            story.append(Paragraph(f"<b>Analysis:</b> {analysis}", normal))
            story.append(Spacer(1, 0.5*cm))

        story.append(Paragraph("TradePro Screener · Automated · Not financial advice", styles["Italic"]))
        doc.build(story)
        return buf.getvalue()
    except Exception as e:
        log.warning("PDF build failed: %s", e)
        return None


def _build_mime(subject: str, html_body: str, pdf_bytes: bytes | None, pdf_name: str) -> bytes:
    """Build a MIME multipart message with HTML body and optional PDF attachment."""
    import email.mime.multipart
    import email.mime.text
    import email.mime.application

    msg = email.mime.multipart.MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = FROM_ADDRESS
    msg["To"] = TO_ADDRESS

    alt = email.mime.multipart.MIMEMultipart("alternative")
    alt.attach(email.mime.text.MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    if pdf_bytes:
        pdf_part = email.mime.application.MIMEApplication(pdf_bytes, _subtype="pdf")
        pdf_part.add_header("Content-Disposition", "attachment", filename=pdf_name)
        msg.attach(pdf_part)

    return msg.as_bytes()


def _send(subject: str, html_body: str, pdf_bytes: bytes | None = None, pdf_name: str = "report.pdf") -> bool:
    for attempt in range(2):
        try:
            ses = _ses_client()
            if pdf_bytes:
                raw = _build_mime(subject, html_body, pdf_bytes, pdf_name)
                ses.send_raw_email(
                    Source=FROM_ADDRESS,
                    Destinations=[TO_ADDRESS],
                    RawMessage={"Data": raw},
                )
            else:
                ses.send_email(
                    Source=FROM_ADDRESS,
                    Destination={"ToAddresses": [TO_ADDRESS]},
                    Message={
                        "Subject": {"Data": subject, "Charset": "UTF-8"},
                        "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}},
                    },
                )
            log.info("Sent%s: %s", " (retry)" if attempt else "", subject)
            return True
        except Exception as e:  # noqa: BLE001
            log.error("SES send failed attempt %d for '%s': %s", attempt + 1, subject, e)
    return False
