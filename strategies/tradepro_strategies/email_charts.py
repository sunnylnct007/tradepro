"""PNG chart generation for the HTML email digest.

Returns base64-encoded data URIs (`data:image/png;base64,...`) that
embed inline in `<img src="...">` tags — no need for cid: attachment
plumbing. Works in Gmail, Apple Mail, Outlook web; Outlook Desktop
strips data: URIs but the plain-text body covers that case.

Three charts get built today:

  1. Bucket donut       BUY / WAIT / AVOID counts at a glance
  2. Holdings P&L bar   Your positions, sorted by % return
  3. BUY sparklines     30-bar closing-price thumbnails per BUY name

Failure mode is silent: any chart that errors returns "" and the
HTML omits the <img>. We never break a digest because matplotlib
can't render a particular asset.
"""
from __future__ import annotations

import base64
import io
from typing import Iterable

# Headless backend — required for SMTP / containers / launchd. The
# import order matters: must come BEFORE pyplot.
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


# Brand colours — match the frontend semantic palette so the email
# and dashboard read like the same product.
COLOUR_UP = "#1fc16b"        # green   — BUY / profit
COLOUR_DOWN = "#e2483a"      # red     — AVOID / loss
COLOUR_NEUTRAL = "#e8a23a"   # amber   — WAIT
COLOUR_TEXT = "#222"
COLOUR_GRID = "#dde2e8"
COLOUR_AXIS = "#999"


def _png_data_url(fig) -> str:
    """Render a matplotlib Figure to a base64 data URL. Closes the
    figure after rendering so the long-running worker doesn't leak
    the matplotlib agg backend's pixel buffers."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def bucket_donut_png(buy_count: int, wait_count: int, avoid_count: int) -> str:
    """At-a-glance count donut: BUY / WAIT / AVOID. Returns "" when
    every bucket is zero (nothing to draw)."""
    total = buy_count + wait_count + avoid_count
    if total == 0:
        return ""
    fig, ax = plt.subplots(figsize=(4.5, 2.4))
    sizes = [buy_count, wait_count, avoid_count]
    colours = [COLOUR_UP, COLOUR_NEUTRAL, COLOUR_DOWN]
    labels = [f"BUY {buy_count}", f"WAIT {wait_count}", f"AVOID {avoid_count}"]
    # Drop any zero bucket so the wedge labels don't pile up.
    triples = [(s, c, l) for s, c, l in zip(sizes, colours, labels) if s > 0]
    sizes = [t[0] for t in triples]
    colours = [t[1] for t in triples]
    labels = [t[2] for t in triples]

    wedges, _ = ax.pie(
        sizes, colors=colours, startangle=90,
        wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 2},
    )
    ax.set_aspect("equal")
    ax.text(0, 0.05, str(total), ha="center", va="center",
            fontsize=22, fontweight="bold", color=COLOUR_TEXT)
    ax.text(0, -0.18, "candidates", ha="center", va="center",
            fontsize=9, color=COLOUR_AXIS)
    ax.legend(
        wedges, labels, loc="center left",
        bbox_to_anchor=(1.05, 0.5), frameon=False, fontsize=10,
    )
    return _png_data_url(fig)


def holdings_pnl_bar_png(holdings: list[dict]) -> str:
    """Horizontal bar chart of T212 holdings sorted by unrealised %.
    Up-coloured for gainers, down-coloured for losers. Returns "" if
    no holding has a P&L number."""
    rows = [
        h for h in (holdings or [])
        if isinstance(h.get("unrealisedPct"), (int, float))
    ]
    if not rows:
        return ""
    rows.sort(key=lambda h: h["unrealisedPct"], reverse=True)
    rows = rows[:14]  # cap to keep the chart legible
    labels = [
        (h.get("instrumentName") or h.get("ticker") or "?")[:24]
        for h in rows
    ]
    pcts = [float(h["unrealisedPct"]) for h in rows]
    colours = [COLOUR_UP if v >= 0 else COLOUR_DOWN for v in pcts]

    fig, ax = plt.subplots(figsize=(7.4, max(2.8, 0.34 * len(rows) + 0.6)))
    y_pos = list(range(len(rows)))
    ax.barh(y_pos, pcts, color=colours, edgecolor="white", height=0.7)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()  # biggest gainer on top
    ax.axvline(0, color=COLOUR_AXIS, linewidth=0.8)
    ax.set_xlabel("Unrealised P&L %", fontsize=10, color=COLOUR_AXIS)
    ax.tick_params(axis="x", colors=COLOUR_AXIS, labelsize=9)
    ax.grid(axis="x", color=COLOUR_GRID, linewidth=0.5, linestyle="--")
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    # Value labels at the bar end so the user reads exact numbers.
    for i, (val, c) in enumerate(zip(pcts, colours)):
        x = val + (0.4 if val >= 0 else -0.4)
        ax.text(x, i, f"{val:+.1f}%", va="center",
                ha="left" if val >= 0 else "right",
                color=c, fontsize=9, fontweight="bold")
    fig.suptitle("Your holdings — unrealised P&L %",
                 fontsize=11, color=COLOUR_TEXT, x=0.05, ha="left")
    return _png_data_url(fig)


def buy_sparklines_png(buy_items: list[dict]) -> str:
    """Strip of mini line charts — one per BUY candidate's recent
    closing prices. Reads `recent_closes` (a list of numbers) from
    the item if present; otherwise falls back to `market_state.closes_30d`.
    Returns "" if no symbol has a usable price series."""
    series_per_symbol: list[tuple[str, list[float]]] = []
    for it in (buy_items or [])[:8]:  # cap at 8 panels per email
        sym = it.get("symbol") or "?"
        closes = it.get("recent_closes") or (
            (it.get("market_state") or {}).get("closes_30d")
        ) or []
        # Defensive: only keep numeric, finite, non-trivial series.
        clean = [float(c) for c in closes
                 if isinstance(c, (int, float)) and c == c]  # NaN check
        if len(clean) >= 5:
            series_per_symbol.append((sym, clean))

    if not series_per_symbol:
        return ""

    n = len(series_per_symbol)
    cols = min(4, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(
        rows, cols,
        figsize=(2.0 * cols, 1.4 * rows + 0.4),
        squeeze=False,
    )
    for idx, (sym, closes) in enumerate(series_per_symbol):
        r, c = divmod(idx, cols)
        ax = axes[r][c]
        first, last = closes[0], closes[-1]
        pct = (last / first - 1.0) * 100.0 if first else 0.0
        line_colour = COLOUR_UP if pct >= 0 else COLOUR_DOWN
        ax.plot(closes, color=line_colour, linewidth=1.6)
        ax.fill_between(range(len(closes)), closes,
                        min(closes), color=line_colour, alpha=0.10)
        ax.set_title(f"{sym}  {pct:+.1f}%",
                     fontsize=10, color=COLOUR_TEXT, loc="left", pad=4)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ("top", "right", "left", "bottom"):
            ax.spines[spine].set_visible(False)

    # Hide any unused panels (when n < rows*cols).
    for idx in range(n, rows * cols):
        r, c = divmod(idx, cols)
        axes[r][c].axis("off")

    fig.suptitle("BUY candidates — recent price action",
                 fontsize=11, color=COLOUR_TEXT, x=0.05, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    return _png_data_url(fig)


def img_block(data_url: str, alt: str = "") -> str:
    """Wrap a data URL in a centred <img> block. Returns "" passthrough
    when the URL is empty so the caller can chain unconditionally."""
    if not data_url:
        return ""
    from html import escape
    return (
        f"<div style='margin:18px 0;text-align:center'>"
        f"<img src='{data_url}' alt='{escape(alt)}' "
        f"style='max-width:100%;height:auto'/>"
        f"</div>"
    )


def _maybe(items: Iterable[dict], key: str) -> bool:
    """Tiny helper used in tests."""
    return any(it.get(key) for it in items)
