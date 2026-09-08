"""Relative context vs the index and the sector ETF — CONTEXT, NOT A GATE.

Owner, 8 Sep: "we shd also compare our candidates in general to the index and
related etf RSI and ATR to make selection more robust". He had just used the
lens twice by hand in one day — MU lagging its bucket while STX ripped, INTC
leading SMH while our screens looked elsewhere. This module makes every
candidate row carry those numbers so the comparison is read, not recomputed.

House rules honoured:
- DISPLAY ONLY. Nothing here gates or ranks. A gating use needs its own
  pre-registered gates doc first (the earnings-v2 lesson).
- Lookbacks are 21/63 sessions and ATR ratios — scale-invariant (addendum).
- Sector ETF comes from config (watch cfg `sector_proxy`, else the
  settings-kv `symbol_sector_etf` map); unmapped names get SPY only.
- Vendor-labelled: if a leg of the comparison is unavailable it is NAMED
  missing, never silently dropped.
"""
import logging

log = logging.getLogger(__name__)

_BENCH_CACHE: dict = {}
_MAP_CACHE: dict | None = None
INDEX = "SPY"


def _rsi(c, n=14):
    """Wilder RSI on closes; None when the series is too short."""
    if len(c) <= n + 1:
        return None
    deltas = [c[i] - c[i - 1] for i in range(1, len(c))]
    up = sum(x for x in deltas[:n] if x > 0) / n
    dn = -sum(x for x in deltas[:n] if x < 0) / n
    for x in deltas[n:]:
        up = (up * (n - 1) + max(x, 0.0)) / n
        dn = (dn * (n - 1) + max(-x, 0.0)) / n
    if dn == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + up / dn), 1)


def _bench(sym):
    if sym not in _BENCH_CACHE:
        try:
            from .cli.preearnings_watch import _daily   # lazy: avoids a cycle
            _BENCH_CACHE[sym] = _daily(sym)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — _daily exits
            log.warning("relative: no bars for benchmark %s: %s", sym, str(exc)[:80])
            _BENCH_CACHE[sym] = None
    return _BENCH_CACHE[sym]


def _sector_etf(sym, override=None):
    global _MAP_CACHE
    if override:
        return override
    if _MAP_CACHE is None:
        try:
            from .cli.push_to_api import load_credentials
            from .cli.preearnings_watch import _kv_get
            b, t = load_credentials()
            _MAP_CACHE = _kv_get(b.rstrip("/"), t, "symbol_sector_etf") or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("relative: sector map unavailable: %s", str(exc)[:80])
            _MAP_CACHE = {}
    return _MAP_CACHE.get(sym)


def _ret(c, n):
    return 100 * (c[-1] / c[-1 - n] - 1) if len(c) > n else None


def relative_context(sym, sector_etf=None):
    """One dict of relative numbers for a candidate row, or None.

    The `line` field is the display form — one decisive sentence, so every
    screen renders the same words instead of re-deriving them.
    """
    try:
        from .cli.preearnings_watch import _daily
        d = _daily(sym, store_only=True)
    except (Exception, SystemExit):  # noqa: BLE001 — _daily raises SystemExit
        return None
    i = len(d.close) - 1
    if i < 64:
        return None
    etf = _sector_etf(sym, sector_etf)
    out = {"index": INDEX, "benchmark": etf, "context_not_a_gate": True,
           "rsi14": _rsi(d.close[-80:]),
           "atr_pct": round(100 * d.atr14[i] / d.close[i], 2)}
    legs = []
    for label, bsym in (("idx", INDEX), ("etf", etf)):
        if not bsym:
            continue
        b = _bench(bsym)
        if b is None or len(b.close) < 64:
            out[f"{label}_missing"] = f"{bsym} bars unavailable"
            continue
        j = len(b.close) - 1
        out[f"rs_21s_vs_{label}_pct"] = (
            round(_ret(d.close, 21) - _ret(b.close, 21), 1))
        out[f"rs_63s_vs_{label}_pct"] = (
            round(_ret(d.close, 63) - _ret(b.close, 63), 1))
        out[f"{label}_rsi14"] = _rsi(b.close[-80:])
        b_atr_pct = 100 * b.atr14[j] / b.close[j]
        if b_atr_pct > 0:
            out[f"atr_ratio_vs_{label}"] = (
                round((100 * d.atr14[i] / d.close[i]) / b_atr_pct, 1))
        if d.dates[-1] != b.dates[-1]:
            out[f"{label}_as_of"] = b.dates[-1]
        legs.append((label, bsym))
    if not legs:
        return None
    bits = [f"RSI {out['rsi14']}"
            + (f" ({etf} {out.get('etf_rsi14')}, SPY {out.get('idx_rsi14')})"
               if etf and out.get('etf_rsi14') is not None
               else f" (SPY {out.get('idx_rsi14')})")]
    ref = "etf" if any(l == "etf" for l, _ in legs) else "idx"
    refname = etf if ref == "etf" else INDEX
    if out.get(f"rs_21s_vs_{ref}_pct") is not None:
        bits.append(f"21s vs {refname} {out[f'rs_21s_vs_{ref}_pct']:+.1f}%")
    if out.get(f"rs_63s_vs_{ref}_pct") is not None:
        bits.append(f"63s {out[f'rs_63s_vs_{ref}_pct']:+.1f}%")
    if out.get(f"atr_ratio_vs_{ref}") is not None:
        bits.append(f"ATR× {out[f'atr_ratio_vs_{ref}']} of {refname}")
    for label, bsym in (("idx", INDEX), ("etf", etf)):
        if out.get(f"{label}_missing"):
            bits.append(f"{bsym} unavailable")
    out["line"] = " · ".join(bits)
    return out
