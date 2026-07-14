"""AWS SES email composition and delivery (SRS Section 12)."""
from __future__ import annotations

import datetime
import logging

import boto3

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
    return _send(subject, body)


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
    return _send(subject, body)


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
</style>
</head><body>
<h1>{title}</h1>
{body_content}
<p style='font-size:11px;color:#aaa;margin-top:24px'>TradePro Screener · Automated · Not financial advice</p>
</body></html>"""


def _send(subject: str, html_body: str) -> bool:
    try:
        ses = _ses_client()
        ses.send_email(
            Source=FROM_ADDRESS,
            Destination={"ToAddresses": [TO_ADDRESS]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}},
            },
        )
        log.info("Sent: %s", subject)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("SES send failed for '%s': %s — retrying", subject, e)
        try:
            ses = _ses_client()
            ses.send_email(
                Source=FROM_ADDRESS,
                Destination={"ToAddresses": [TO_ADDRESS]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}},
                },
            )
            log.info("Retry succeeded: %s", subject)
            return True
        except Exception as e2:  # noqa: BLE001
            log.error("SES retry failed: %s", e2)
            return False
