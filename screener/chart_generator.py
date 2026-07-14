"""Generate embedded base64 price+RSI charts for email."""
from __future__ import annotations

import base64
import io
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as patches


# ── helpers ────────────────────────────────────────────────────────────────

def _closes(bars):
    return [float(b["c"]) for b in bars if b.get("c")]

def _highs(bars):
    return [float(b["h"]) for b in bars if b.get("h")]

def _lows(bars):
    return [float(b["l"]) for b in bars if b.get("l")]

def _volumes(bars):
    return [float(b["v"]) for b in bars if b.get("v")]

def _sma(closes, period):
    if len(closes) < period:
        return [None] * len(closes)
    result = [None] * (period - 1)
    for i in range(period - 1, len(closes)):
        result.append(sum(closes[i - period + 1: i + 1]) / period)
    return result

def _rsi_series(closes, period=14):
    if len(closes) < period + 1:
        return [None] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    result = [None] * period
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(closes)):
        if avg_l == 0:
            result.append(100.0)
        else:
            rs = avg_g / avg_l
            result.append(round(100 - 100 / (1 + rs), 2))
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
    return result

def _support_resistance(highs, lows, lookback=60):
    """Simple swing high/low support & resistance levels."""
    if len(highs) < lookback:
        lookback = len(highs)
    recent_highs = highs[-lookback:]
    recent_lows = lows[-lookback:]

    # Resistance: top 2 swing highs
    resistance = sorted(set([max(recent_highs)]), reverse=True)[:1]
    # Support: bottom 2 swing lows
    support = sorted(set([min(recent_lows)]))[:1]

    # Add mid-range level
    mid = (max(recent_highs) + min(recent_lows)) / 2
    return support, resistance, mid


# ── main chart function ────────────────────────────────────────────────────

def generate_chart_b64(bars: list[dict], ticker: str, window: int = 90) -> str:
    """Return a base64-encoded PNG string of a price+RSI chart (last `window` bars)."""
    if len(bars) < 20:
        return ""

    bars = bars[-window:] if len(bars) > window else bars
    n = len(bars)
    x = list(range(n))

    closes = _closes(bars)
    highs  = _highs(bars)
    lows   = _lows(bars)
    vols   = _volumes(bars)

    ma20  = _sma(closes, 20)
    ma50  = _sma(closes, 50)
    ma200 = _sma(closes, 200)
    rsi_vals = _rsi_series(closes, 14)

    support, resistance, mid_level = _support_resistance(highs, lows, lookback=min(60, n))

    # ── figure layout ──────────────────────────────────────────────────────
    fig = plt.figure(figsize=(9, 5.5), facecolor="#1a1a2e")
    gs = gridspec.GridSpec(3, 1, height_ratios=[3, 1, 0.6], hspace=0.08)
    ax_price = fig.add_subplot(gs[0])
    ax_rsi   = fig.add_subplot(gs[1], sharex=ax_price)
    ax_vol   = fig.add_subplot(gs[2], sharex=ax_price)

    for ax in [ax_price, ax_rsi, ax_vol]:
        ax.set_facecolor("#0d0d1a")
        ax.tick_params(colors="#aaaaaa", labelsize=7)
        for spine in ax.spines.values():
            spine.set_color("#333355")

    # ── candlestick bars ───────────────────────────────────────────────────
    for i in range(n):
        o = float(bars[i].get("o", closes[i]))
        c = closes[i]
        h = highs[i] if i < len(highs) else c
        l = lows[i]  if i < len(lows)  else c
        color = "#26a69a" if c >= o else "#ef5350"
        ax_price.plot([i, i], [l, h], color=color, linewidth=0.8, alpha=0.7)
        ax_price.bar(i, abs(c - o), bottom=min(o, c), color=color, width=0.6, alpha=0.9)

    # ── MAs ───────────────────────────────────────────────────────────────
    def _plot_ma(ax, vals, color, label):
        xs = [i for i, v in enumerate(vals) if v is not None]
        ys = [v for v in vals if v is not None]
        if xs:
            ax.plot(xs, ys, color=color, linewidth=1.2, label=label, alpha=0.85)

    _plot_ma(ax_price, ma20,  "#f6c90e", "MA20")
    _plot_ma(ax_price, ma50,  "#00bcd4", "MA50")
    _plot_ma(ax_price, ma200, "#ff7043", "MA200")

    # ── support / resistance ───────────────────────────────────────────────
    for s in support:
        ax_price.axhline(s, color="#26a69a", linewidth=0.8, linestyle="--", alpha=0.6)
        ax_price.text(n - 1, s, f" S {s:.2f}", color="#26a69a", fontsize=6.5, va="center")
    for r in resistance:
        ax_price.axhline(r, color="#ef5350", linewidth=0.8, linestyle="--", alpha=0.6)
        ax_price.text(n - 1, r, f" R {r:.2f}", color="#ef5350", fontsize=6.5, va="center")
    ax_price.axhline(mid_level, color="#9e9e9e", linewidth=0.5, linestyle=":", alpha=0.5)
    ax_price.text(n - 1, mid_level, f" Mid {mid_level:.2f}", color="#9e9e9e", fontsize=6, va="center")

    # current price line
    ax_price.axhline(closes[-1], color="#ffffff", linewidth=0.6, linestyle="--", alpha=0.4)

    ax_price.set_ylabel("Price", color="#aaaaaa", fontsize=8)
    ax_price.legend(loc="upper left", fontsize=6.5, facecolor="#1a1a2e",
                    edgecolor="#333355", labelcolor="white", framealpha=0.7)
    ax_price.set_title(f"{ticker}  —  last {n} sessions", color="#eeeeee", fontsize=10, pad=6)

    # ── RSI panel ─────────────────────────────────────────────────────────
    xs_rsi = [i for i, v in enumerate(rsi_vals) if v is not None]
    ys_rsi = [v for v in rsi_vals if v is not None]
    if xs_rsi:
        ax_rsi.plot(xs_rsi, ys_rsi, color="#ce93d8", linewidth=1.1)
        ax_rsi.fill_between(xs_rsi, ys_rsi, 50, where=[v >= 50 for v in ys_rsi],
                            color="#ce93d8", alpha=0.15)
    ax_rsi.axhline(70, color="#ef5350", linewidth=0.7, linestyle="--", alpha=0.6)
    ax_rsi.axhline(30, color="#26a69a", linewidth=0.7, linestyle="--", alpha=0.6)
    ax_rsi.axhline(50, color="#666688", linewidth=0.5, linestyle=":", alpha=0.5)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI(14)", color="#aaaaaa", fontsize=7)
    ax_rsi.text(1, 71, "70", color="#ef5350", fontsize=6)
    ax_rsi.text(1, 31, "30", color="#26a69a", fontsize=6)

    # ── Volume panel ──────────────────────────────────────────────────────
    avg_v = sum(vols) / len(vols) if vols else 1
    vol_colors = ["#26a69a" if closes[i] >= float(bars[i].get("o", closes[i]))
                  else "#ef5350" for i in range(n)]
    ax_vol.bar(x, vols, color=vol_colors, width=0.7, alpha=0.7)
    ax_vol.axhline(avg_v, color="#f6c90e", linewidth=0.7, linestyle="--", alpha=0.7)
    ax_vol.set_ylabel("Vol", color="#aaaaaa", fontsize=7)
    ax_vol.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda v, _: f"{v/1e6:.1f}M" if v >= 1e6 else f"{v/1e3:.0f}K"))

    plt.setp(ax_price.get_xticklabels(), visible=False)
    plt.setp(ax_rsi.get_xticklabels(), visible=False)

    # ── export ────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")
