"""TradePro Screener — daily run entry point for MCP-driven execution.

This script is invoked by the Claude Code Routine. It receives pre-fetched
IBKR data as a JSON file (written by the Claude routine from MCP responses),
runs the wheel + swing screener, calls Claude API, and sends emails via SES.

Usage (from routine):
    python screener/daily_run.py --input-file /tmp/screener_data.json

Input JSON format:
    {
      "run_date": "2026-07-12",
      "stocks": {
        "NVDA": {
          "conid": 4815747,
          "snapshot": { <MCP get_price_snapshot response> },
          "history":  { <MCP get_price_history response> },
          "earnings_date": "2026-08-20"   // or null
        },
        ...
      },
      "spy_history": { <MCP get_price_history response for SPY> }
    }
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import os
import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("screener.daily_run")

TOP_N = 5


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-file", required=True, help="Path to JSON file with IBKR data")
    parser.add_argument("--dry-run", action="store_true", help="Skip email sending; print HTML to stdout")
    args = parser.parse_args()

    with open(args.input_file) as f:
        payload = json.load(f)

    run_date = datetime.date.fromisoformat(payload.get("run_date", datetime.date.today().isoformat()))
    stocks = payload["stocks"]
    spy_history = payload.get("spy_history", {})

    log.info("=== TradePro Screener: %s — %d stocks ===", run_date, len(stocks))

    # Import screener modules (same directory)
    _add_screener_to_path()
    from ibkr_mcp_adapter import bars_from_mcp, snapshot_to_fields, estimate_put_premium_pct
    from wheel_screener import score_wheel
    from swing_screener import score_swing
    from email_sender import send_wheel_email, send_swing_email

    spy_bars = bars_from_mcp(spy_history) if spy_history else []

    wheel_passed, swing_passed = [], []

    for ticker, data in stocks.items():
        snap_fields = snapshot_to_fields(data.get("snapshot", {}))
        bars = bars_from_mcp(data.get("history", {}))
        earnings_date = data.get("earnings_date")

        price = snap_fields["price"]
        if not price:
            log.warning("%s: no price — skipping", ticker)
            continue

        log.info(
            "%s price=%.2f ivp=%.1f div=%.1f%% hv=%.1f%% bars=%d earnings=%s",
            ticker, price,
            snap_fields["iv_percentile_52w"],
            snap_fields["dividend_yield_pct"],
            snap_fields["historical_vol_annual"],
            len(bars), earnings_date,
        )

        premium_pct = estimate_put_premium_pct(bars, price)

        wc = score_wheel(
            ticker=ticker,
            price=price,
            ivr=snap_fields["iv_percentile_52w"],
            div_yield=snap_fields["dividend_yield_pct"],
            bars=bars,
            low_52w=snap_fields["low_52w"],
            high_52w=snap_fields["high_52w"],
            premium_pct=premium_pct,
            earnings_date=earnings_date,
        )
        if wc.passed_gate():
            wheel_passed.append(wc)
        else:
            log.info("%s wheel excluded: %s", ticker, wc.gate_fail_reason)

        sc = score_swing(
            ticker=ticker,
            price=price,
            bars=bars,
            spy_bars=spy_bars,
            earnings_date=earnings_date,
        )
        if sc.passed_gate():
            swing_passed.append(sc)
        else:
            log.info("%s swing excluded: %s", ticker, sc.gate_fail_reason)

    # Sort and take top 5
    wheel_top = sorted(wheel_passed, key=lambda c: c.score, reverse=True)[:TOP_N]
    swing_top = sorted(swing_passed, key=lambda c: c.score, reverse=True)[:TOP_N]

    log.info("Wheel: %d qualified → top %d: %s",
             len(wheel_passed), len(wheel_top), [c.ticker for c in wheel_top])
    log.info("Swing: %d qualified → top %d: %s",
             len(swing_passed), len(swing_top), [c.ticker for c in swing_top])

    # Overlap
    wheel_tickers = {c.ticker for c in wheel_top}
    swing_tickers = {c.ticker for c in swing_top}
    overlap = wheel_tickers & swing_tickers
    if overlap:
        log.info("Dual candidates: %s", overlap)
    for c in wheel_top:
        c.dual_candidate = c.ticker in overlap
    for c in swing_top:
        c.dual_candidate = c.ticker in overlap

    # Claude explanations
    _add_explanations(wheel_top, swing_top)

    # Send / dry-run
    if args.dry_run:
        _dry_run_output(wheel_top, swing_top, run_date)
    else:
        wheel_ok = send_wheel_email(wheel_top, run_date)
        swing_ok = send_swing_email(swing_top, run_date)
        log.info("Emails sent — wheel: %s  swing: %s", wheel_ok, swing_ok)

    result = {
        "run_date": run_date.isoformat(),
        "tickers_screened": len(stocks),
        "wheel_top": [c.ticker for c in wheel_top],
        "swing_top": [c.ticker for c in swing_top],
        "dual_candidates": list(overlap),
        "wheel_count": len(wheel_top),
        "swing_count": len(swing_top),
    }
    print(json.dumps(result))
    return 0


CLAUDE_ENABLED = False  # set to True to re-enable AI explanations


def _add_explanations(wheel_top, swing_top):
    """Call Claude API for 2-sentence explanations per candidate."""
    if not CLAUDE_ENABLED:
        for c in wheel_top + swing_top:
            c.explanation = "See metrics above."
        return
    try:
        import anthropic
        from secrets import get_secret
        from claude_client import explain_wheel, explain_swing
        client = anthropic.Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))
        for c in wheel_top:
            c.explanation = explain_wheel(
                client, c.ticker, c.price, c.ivr, c.div_yield, c.score,
                c.volatility_label, c.close_guidance, c.trend_status, c.dist_52w_low_pct,
            )
        for c in swing_top:
            c.explanation = explain_swing(
                client, c.ticker, c.price, c.rsi_val, c.ma20_dist_pct, c.score,
                c.trend_status, c.vol_signal, c.rs_status,
            )
    except Exception as e:  # noqa: BLE001
        log.warning("Claude explanations failed: %s — using fallback", e)
        for c in wheel_top + swing_top:
            if not hasattr(c, "explanation"):
                c.explanation = "See metrics above."


def _dry_run_output(wheel_top, swing_top, run_date):
    from email_sender import _build_wheel_html, _build_swing_html
    date_str = run_date.strftime("%d %b %Y")
    log.info("--- DRY RUN: Wheel email ---")
    log.info("%s", _build_wheel_html(wheel_top, date_str)[:500])
    log.info("--- DRY RUN: Swing email ---")
    log.info("%s", _build_swing_html(swing_top, date_str)[:500])


def _add_screener_to_path():
    screener_dir = os.path.dirname(os.path.abspath(__file__))
    if screener_dir not in sys.path:
        sys.path.insert(0, screener_dir)


if __name__ == "__main__":
    sys.exit(main())
