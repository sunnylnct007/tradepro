"""TradePro Screener — AWS Lambda entry point.

Orchestrates the full daily screening run per SRS v1.1 Section 6.3:
  1. Read TradePro-Screen watchlist from IBKR
  2. Fetch price/IV/volume/earnings per ticker
  3. Apply wheel gate + score → top 5
  4. Apply swing gate + score → top 5
  5. Detect overlap (dual candidates)
  6. Call Claude for 2-sentence explanations
  7. Send wheel + swing HTML emails via SES
  8. Log run summary to CloudWatch
"""
from __future__ import annotations

import datetime
import logging
import os
import sys

# CloudWatch structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("screener.main")

WATCHLIST_NAME = "TradePro-Screen"
WATCHLIST_ID = int(os.environ.get("IBKR_WATCHLIST_ID", "101"))
TOP_N = 5
SPY_TICKER = "SPY"


def handler(event: dict, context) -> dict:
    run_date = datetime.date.today()
    log.info("=== TradePro Screener run start: %s ===", run_date.isoformat())

    try:
        return _run(run_date)
    except Exception as e:  # noqa: BLE001
        log.exception("Fatal error in screener run: %s", e)
        return {"status": "error", "message": str(e)}


def _run(run_date: datetime.date) -> dict:
    from secrets import get_secret
    from ibkr_client import IBKRClient
    from wheel_screener import score_wheel
    from swing_screener import score_swing
    from claude_client import explain_wheel, explain_swing
    from email_sender import send_wheel_email, send_swing_email
    import requests
    import anthropic

    # --- Setup clients ---
    ibkr_base = get_secret("IBKR_CLIENT_PORTAL_URL")
    ibkr = IBKRClient(ibkr_base)

    claude = anthropic.Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))

    # --- Step 2: Read watchlist ---
    tickers = _get_tickers(ibkr)
    log.info("Watchlist '%s': %d tickers: %s", WATCHLIST_NAME, len(tickers), tickers)

    # --- Step 3: Fetch market data ---
    spy_bars = _fetch_bars(ibkr, SPY_TICKER)

    stock_data = {}
    for ticker in tickers:
        data = _fetch_stock_data(ibkr, ticker)
        if data:
            stock_data[ticker] = data
        else:
            log.warning("%s skipped — data fetch failed", ticker)

    log.info("Data fetched for %d/%d tickers", len(stock_data), len(tickers))

    # --- Steps 4 & 5: Screen ---
    wheel_candidates = []
    swing_candidates = []

    for ticker, d in stock_data.items():
        wc = score_wheel(
            ticker=ticker,
            price=d["price"],
            ivr=d["ivr"],
            div_yield=d["div_yield"],
            bars=d["bars"],
            low_52w=d["low_52w"],
            high_52w=d["high_52w"],
            premium_pct=d["premium_pct"],
            earnings_date=d["earnings_date"],
        )
        if wc.passed_gate():
            wheel_candidates.append(wc)

        sc = score_swing(
            ticker=ticker,
            price=d["price"],
            bars=d["bars"],
            spy_bars=spy_bars,
            earnings_date=d["earnings_date"],
        )
        if sc.passed_gate():
            swing_candidates.append(sc)

    # Sort by score descending, take top 5
    wheel_top = sorted(wheel_candidates, key=lambda c: c.score, reverse=True)[:TOP_N]
    swing_top = sorted(swing_candidates, key=lambda c: c.score, reverse=True)[:TOP_N]

    log.info("Wheel: %d passed gate → top %d: %s",
             len(wheel_candidates), len(wheel_top), [c.ticker for c in wheel_top])
    log.info("Swing: %d passed gate → top %d: %s",
             len(swing_candidates), len(swing_top), [c.ticker for c in swing_top])

    # --- Step 6: Overlap detection ---
    wheel_tickers = {c.ticker for c in wheel_top}
    swing_tickers = {c.ticker for c in swing_top}
    overlap = wheel_tickers & swing_tickers
    if overlap:
        log.info("Dual candidates (overlap): %s", overlap)
    for c in wheel_top:
        c.dual_candidate = c.ticker in overlap
    for c in swing_top:
        c.dual_candidate = c.ticker in overlap

    # --- Step 7: Claude explanations (max 10 calls: 5 wheel + 5 swing) ---
    for c in wheel_top:
        c.explanation = explain_wheel(
            claude, c.ticker, c.price, c.ivr, c.div_yield, c.score,
            c.volatility_label, c.close_guidance, c.trend_status, c.dist_52w_low_pct,
        )

    for c in swing_top:
        c.explanation = explain_swing(
            claude, c.ticker, c.price, c.rsi_val, c.ma20_dist_pct, c.score,
            c.trend_status, c.vol_signal, c.rs_status,
        )

    # --- Steps 8 & 9: Send emails ---
    wheel_ok = send_wheel_email(wheel_top, run_date)
    swing_ok = send_swing_email(swing_top, run_date)

    # --- Step 10: Log run summary ---
    summary = {
        "status": "ok",
        "run_date": run_date.isoformat(),
        "tickers_screened": len(stock_data),
        "wheel_candidates": len(wheel_top),
        "swing_candidates": len(swing_top),
        "dual_candidates": list(overlap),
        "wheel_email_sent": wheel_ok,
        "swing_email_sent": swing_ok,
        "wheel_top": [c.ticker for c in wheel_top],
        "swing_top": [c.ticker for c in swing_top],
    }
    log.info("=== Run complete: %s ===", summary)
    return summary


def _get_tickers(ibkr) -> list[str]:
    # Try by ID first (confirmed ID 101), fall back to name search
    try:
        tickers = ibkr.get_watchlist_tickers(WATCHLIST_ID)
        if tickers:
            return tickers
    except Exception as e:  # noqa: BLE001
        log.warning("Watchlist ID %d fetch failed: %s — trying by name", WATCHLIST_ID, e)

    wl_id = ibkr.find_watchlist_id(WATCHLIST_NAME)
    if wl_id is None:
        log.error("Watchlist '%s' not found — aborting", WATCHLIST_NAME)
        raise RuntimeError(f"Watchlist '{WATCHLIST_NAME}' not found")

    tickers = ibkr.get_watchlist_tickers(wl_id)
    if not tickers:
        log.error("Watchlist '%s' returned 0 stocks — aborting", WATCHLIST_NAME)
        raise RuntimeError(f"Watchlist '{WATCHLIST_NAME}' is empty")

    return tickers


def _fetch_bars(ibkr, ticker: str) -> list[dict]:
    conid = ibkr.resolve_conid(ticker)
    if not conid:
        log.warning("%s could not resolve conid", ticker)
        return []
    return ibkr.get_daily_bars(conid, period="1y")


def _fetch_stock_data(ibkr, ticker: str) -> dict | None:
    try:
        conid = ibkr.resolve_conid(ticker)
        if not conid:
            log.warning("%s: no conid", ticker)
            return None

        snap = ibkr.get_snapshot(conid)
        price = _parse_float(snap.get("31"))
        if not price:
            log.warning("%s: no price in snapshot", ticker)
            return None

        bars = ibkr.get_daily_bars(conid, period="1y")
        if len(bars) < 30:
            log.warning("%s: only %d bars (need ≥ 30)", ticker, len(bars))
            return None

        low_52w = _parse_float(snap.get("7294")) or _min_close(bars, 252)
        high_52w = _parse_float(snap.get("7293")) or _max_close(bars, 252)

        earnings_date = ibkr.get_next_earnings_date(conid)

        # IV as 30d HV annualised (proxy — IBKR snapshot IV not always available)
        import math
        closes = [float(b["c"]) for b in bars if b.get("c")]
        if len(closes) >= 22:
            rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, 22)]
            daily_vol = (sum(r ** 2 for r in rets) / len(rets)) ** 0.5
            current_iv = daily_vol * math.sqrt(252) * 100
        else:
            current_iv = 20.0

        from indicators import iv_rank as calc_ivr
        ivr_val = calc_ivr(bars, current_iv) or 0.0

        # Dividend yield from snapshot field 7286 (annual div / price * 100)
        div_raw = _parse_float(snap.get("7286"))
        div_yield = (div_raw / price * 100) if div_raw and price else 0.0

        # ATM put premium % estimate
        premium_pct = ibkr.get_atm_put_premium_pct(conid, price)

        log.info(
            "%s conid=%d price=%.2f ivr=%.1f div=%.1f%% earnings=%s bars=%d",
            ticker, conid, price, ivr_val, div_yield, earnings_date, len(bars),
        )

        return {
            "conid": conid,
            "price": price,
            "bars": bars,
            "low_52w": low_52w,
            "high_52w": high_52w,
            "earnings_date": earnings_date,
            "ivr": ivr_val,
            "div_yield": div_yield,
            "premium_pct": premium_pct,
        }

    except Exception as e:  # noqa: BLE001
        log.warning("%s data fetch failed: %s", ticker, e)
        return None


def _parse_float(val) -> float:
    try:
        return float(str(val).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


def _min_close(bars: list[dict], n: int) -> float:
    closes = [float(b["c"]) for b in bars[-n:] if b.get("c")]
    return min(closes) if closes else 0.0


def _max_close(bars: list[dict], n: int) -> float:
    closes = [float(b["c"]) for b in bars[-n:] if b.get("c")]
    return max(closes) if closes else 0.0
