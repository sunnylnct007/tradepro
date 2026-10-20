"""Does an ENTRY have edge — independent of any exit?

Owner, 20 Oct 2026: *"we basically need to capture trading signals so we can
evaluate any future strategy easily"*.

WHY THIS IS A MODULE AND NOT A SCRIPT. On 20 Oct this question was answered for
swing and momentum by a throwaway file in /tmp. The answer was decisive — both
rules bought moments that were WORSE than holding the same names at random —
and it arrived in a day, after three weeks had been spent hardening the
execution path for rules that never had an edge. That is the single most useful
measurement this project has made, and it was not repeatable by anyone.

WHAT IT NEEDS: a symbol, a signal date, and bars. Nothing else.

No exit rule, no stop, no target, no position cap, no broker, no lane. That is
the point: `signal_replay` must import a strategy's own exit module and so only
works for strategies already coded here (STRATEGY_MODULES is a hardcoded map of
two). This works on ANY list of (symbol, date) pairs — a strategy spec nobody
has built yet, a CSV of hand-taken trades, another agent's proposal — which is
what "evaluate any future strategy easily" actually requires.

TWO CONTROLS, BECAUSE A POSITIVE NUMBER PROVES NOTHING ON ITS OWN.

  * Control A — the SAME symbol, unconditionally: its mean forward return at
    the same horizon across all its other bars. Strips out "we picked names
    that drift up". This is the control that killed swing and momentum: their
    signal returns were negative while Control A was POSITIVE at every horizon.
  * Control B — a BENCHMARK over the identical dates (SPY by default). Strips
    out the regime. A long-only rule that merely tracks a rising market has
    found nothing.

A rule must beat both. Beating neither is what "worse than random" means.

CONCENTRATION IS REPORTED, NOT ASSUMED AWAY. `drop_top` re-computes the mean
without the single largest-deviation symbol. Small samples let one name carry a
whole result, and this desk has curve-fit on exactly that before, so the number
is always shown rather than left for someone to think of.

WHAT IT CANNOT DO. It measures entries over a fixed horizon. It says nothing
about whether a given exit converts that into money, nothing about costs or
slippage, and nothing about regimes it has no data for. A pass is a reason to
continue, never a reason to fund.
"""
from __future__ import annotations

import statistics as _st
from typing import Callable, Iterable, Sequence

#: Trading-bar horizons measured by default. Short ones have the most data in a
#: young archive; longer ones are where a slow rule would show up.
HORIZONS: tuple[int, ...] = (1, 3, 5, 10, 20)

#: Default Control B. Overridable — an FX or index rule needs its own.
BENCHMARK = "SPY"


def forward_return(closes: Sequence[float], i: int, h: int) -> float | None:
    """Percent return from bar i to bar i+h, or None if it runs off the end."""
    j = i + h
    if j >= len(closes) or i < 0 or not closes[i] or closes[i] <= 0:
        return None
    return 100.0 * (closes[j] / closes[i] - 1.0)


def unconditional_mean(closes: Sequence[float], h: int, skip: int) -> float | None:
    """CONTROL A — what this symbol does at horizon h from any OTHER bar.

    `skip` excludes the signal bar itself, so the control cannot include the
    observation it is controlling for.
    """
    vals = [forward_return(closes, i, h) for i in range(len(closes)) if i != skip]
    vals = [v for v in vals if v is not None]
    return _st.mean(vals) if vals else None


def evaluate(
    signals: Iterable[tuple[str, str]],
    load: Callable[[str], tuple[list[str], list[float]] | None],
    *,
    horizons: Sequence[int] = HORIZONS,
    benchmark: str | None = BENCHMARK,
) -> dict:
    """Grade a set of entries. `signals` is (symbol, signal_date) pairs.

    `load` maps a symbol to (dates, closes) — the same shape
    `signal_replay.golden_loader()` returns, so the golden store's poison
    checks apply here too.

    FIRST APPEARANCE IS THE CALLER'S JOB. Boards re-publish a standing signal
    daily; counting repeats would weight whatever the rule kept liking. The
    archive helpers below de-duplicate; a caller passing raw rows must too.
    """
    bench = load(benchmark) if benchmark else None
    rows: dict[int, list[tuple[str, float, float | None, float | None]]] = {
        h: [] for h in horizons
    }
    unmeasured: list[dict] = []

    for sym, day in signals:
        if not sym or not day:
            unmeasured.append({"symbol": sym, "date": day, "why": "missing symbol or date"})
            continue
        series = load(sym)
        if not series:
            unmeasured.append({"symbol": sym, "date": day, "why": "no bars in the store"})
            continue
        dates, closes = series
        if day not in dates:
            unmeasured.append({"symbol": sym, "date": day,
                               "why": f"signal bar not in the store (ends {dates[-1] if dates else 'empty'})"})
            continue
        i = dates.index(day)
        measured_any = False
        for h in horizons:
            sig = forward_return(closes, i, h)
            if sig is None:
                continue          # not enough forward data YET — not a failure
            measured_any = True
            ctrl_a = unconditional_mean(closes, h, skip=i)
            ctrl_b = None
            if bench and day in bench[0]:
                ctrl_b = forward_return(bench[1], bench[0].index(day), h)
            rows[h].append((sym, sig, ctrl_a, ctrl_b))
        if not measured_any:
            unmeasured.append({"symbol": sym, "date": day,
                               "why": "signal too recent — no forward bars yet"})

    out: dict[int, dict] = {}
    for h in horizons:
        r = rows[h]
        if not r:
            continue
        sig = _st.mean(x[1] for x in r)
        a_vals = [x[2] for x in r if x[2] is not None]
        b_vals = [x[3] for x in r if x[3] is not None]
        ctrl_a = _st.mean(a_vals) if a_vals else None
        ctrl_b = _st.mean(b_vals) if b_vals else None
        # CONCENTRATION: the mean without the single biggest deviation.
        drop_top = None
        if len(r) > 1:
            far = max(r, key=lambda x: abs(x[1] - sig))
            rest = [x[1] for x in r if x[0] != far[0]]
            if rest:
                drop_top = {"symbol": far[0], "its_return": round(far[1], 2),
                            "mean_without": round(_st.mean(rest), 3),
                            "sign_flips": (sig > 0) != (_st.mean(rest) > 0)}
        out[h] = {
            "n": len(r),
            "signal_pct": round(sig, 3),
            "control_symbol_pct": None if ctrl_a is None else round(ctrl_a, 3),
            "vs_symbol": None if ctrl_a is None else round(sig - ctrl_a, 3),
            "control_benchmark_pct": None if ctrl_b is None else round(ctrl_b, 3),
            "vs_benchmark": None if ctrl_b is None else round(sig - ctrl_b, 3),
            "hit_rate_pct": round(100.0 * sum(1 for x in r if x[1] > 0) / len(r), 1),
            "beats_symbol": None if ctrl_a is None else sig > ctrl_a,
            "beats_benchmark": None if ctrl_b is None else sig > ctrl_b,
            "drop_top": drop_top,
        }

    measured = {x[0] for h in out for x in rows[h]}
    return {
        "horizons": out,
        "signals_measured": len(measured),
        "unmeasured": unmeasured,
        "benchmark": benchmark,
        "caveats": [
            "Entries only. Says nothing about whether an exit converts this into money.",
            "No costs, no slippage — treat any positive number as a ceiling.",
            "Control A is the same symbol at other times; Control B is the benchmark "
            "over identical dates. A rule must beat BOTH.",
            f"{len(unmeasured)} signal(s) could not be measured and are listed, not dropped."
            if unmeasured else "Every signal was measurable.",
        ],
    }


def first_appearances(boards: dict[str, dict]) -> list[tuple[str, str]]:
    """Archive boards -> (symbol, signal_bar), each symbol's FIRST appearance.

    Same rule `signal_replay` uses: a standing signal re-published daily would
    otherwise be counted once per day and weight whatever the rule kept liking.
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for day in sorted(boards):
        info = boards[day] or {}
        bar = info.get("signal_bar")
        for sym in (info.get("symbols") or []):
            if sym and sym not in seen:
                seen.add(sym)
                out.append((sym, bar))
    return out


def format_table(result: dict, title: str) -> str:
    """One readable block. The controls sit next to the signal on purpose —
    a signal column on its own is the number that misleads."""
    lines = [
        "=" * 78,
        f"{title} — entries only, no stop/target/trail",
        f"{'h':>3}{'n':>6}{'signal%':>10}{'symbol%':>10}{'vs sym':>9}"
        f"{'bench%':>9}{'vs bench':>10}{'hit%':>7}",
    ]
    for h, r in sorted(result["horizons"].items()):
        def f(v, w=9):
            return f"{v:>{w}.2f}" if isinstance(v, (int, float)) else f"{'—':>{w}}"
        lines.append(
            f"{h:>3}{r['n']:>6}{f(r['signal_pct'],10)}{f(r['control_symbol_pct'],10)}"
            f"{f(r['vs_symbol'])}{f(r['control_benchmark_pct'])}"
            f"{f(r['vs_benchmark'],10)}{r['hit_rate_pct']:>7.0f}"
        )
    for h, r in sorted(result["horizons"].items()):
        d = r.get("drop_top")
        if d and d["sign_flips"]:
            lines.append(
                f"  ! h={h}: dropping {d['symbol']} ({d['its_return']:+.1f}%) flips the "
                f"sign to {d['mean_without']:+.2f}% — one name carries this result")
    for c in result.get("caveats", []):
        lines.append(f"  ! {c}")
    return "\n".join(lines)
