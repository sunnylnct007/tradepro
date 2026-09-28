"""What has THIS rule actually done on THIS name — as a distribution, not a forecast.

Owner, 27 Sep 2026: *"what can we do to evaluate the simulation of p/l for the
selected symbol if we decide to trade"*.

THE TRAP THIS IS BUILT AROUND. A per-symbol P&L simulation is the easiest place
on this desk to produce a confident, false number. Most names have fewer than
thirty historical instances of their own signal; that is anecdote, not a
distribution. "AES: expected +1.4%" off eleven trades would be acted on, and
eleven trades cannot support it.

So this reports the SAMPLE SIZE FIRST and refuses to state a central estimate
below a threshold — the same rule the wheel's expectancy column follows, where
a name with no breach history shows a dash rather than a zero.

AND IT IS A DISTRIBUTION, NOT AN EXPECTATION. "median +1.2%, worst -14%, 68%
positive over 34 instances" is a shape you can size against. "+1.4% expected"
is a forecast, and this desk does not have forecasts.

The rule comes from the SIGNAL MODULE the live sleeve imports, so an outlook
can never describe a rule that is not being traded.
"""
from __future__ import annotations

import statistics as st
from dataclasses import asdict, dataclass, field

#: Below this many instances a central estimate is REFUSED. Thirty is not a
#: magic number; it is the point below which one outlier moves the median, and
#: the honest answer is "we do not know about this name" rather than a figure.
MIN_FOR_ESTIMATE = 30

#: Below this there is nothing to report at all.
MIN_TO_REPORT = 5


@dataclass
class Outlook:
    symbol: str
    strategy: str
    instances: int
    #: True only when `instances` clears MIN_FOR_ESTIMATE. Read this FIRST.
    estimate_is_supported: bool
    win_rate_pct: float | None = None
    median_pct: float | None = None
    mean_pct: float | None = None
    worst_pct: float | None = None
    best_pct: float | None = None
    median_hold_sessions: int | None = None
    #: The spread a reader should size against, not the average.
    p10_pct: float | None = None
    p90_pct: float | None = None
    first_signal: str | None = None
    last_signal: str | None = None
    note: str = ""
    rule_source: str = ""
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    i = max(0, min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def swing_outlook(symbol: str, closes: list[float], highs: list[float],
                  lows: list[float], opens: list[float],
                  dates: list[str]) -> Outlook:
    """Every historical instance of the LIVE swing rule on this name.

    Non-overlapping: a new entry is only taken once the previous trade is out,
    which is how it would actually be traded. A corrupt bar inside the hold
    discards the whole trade rather than booking the fiction — the same guard
    the momentum replay earned after a >35% session produced a -98% "worst
    trade".
    """
    from .signals.mean_reversion import (
        BB_WINDOW as W, MAX_HOLD, SIGMA, STOP_PCT, TREND_WINDOW, sma)

    rets: list[float] = []
    bars: list[int] = []
    when: list[str] = []
    n = len(closes)
    i = TREND_WINDOW + 10
    while i < n - 1:
        window = closes[i - W + 1:i + 1]
        if len(window) < W:
            i += 1
            continue
        sd = st.pstdev(window)
        if not (sd > 0 and closes[i] > sma(closes, i, TREND_WINDOW)):
            i += 1
            continue
        if (closes[i] - st.fmean(window)) / sd > -SIGMA:
            i += 1
            continue
        entry = closes[i]
        stop = entry * (1 - STOP_PCT)
        res = None
        for j in range(i + 1, min(n, i + MAX_HOLD + 1)):
            if closes[j - 1] <= 0 or abs(closes[j] / closes[j - 1] - 1) > 0.35:
                res = "corrupt"
                break
            tgt = sma(closes, j, W)
            if lows[j] <= stop:
                res = (100 * (min(stop, opens[j]) / entry - 1), j - i)
                break
            if highs[j] >= tgt:
                res = (100 * (max(tgt, opens[j]) / entry - 1), j - i)
                break
        if res == "corrupt":
            i = j + 1
            continue
        if res is None:
            j = min(n - 1, i + MAX_HOLD)
            res = (100 * (closes[j] / entry - 1), j - i)
        rets.append(res[0])
        bars.append(res[1])
        when.append(dates[i])
        i += max(1, res[1]) + 1

    return _summarise(symbol, "mean_reversion_swing", rets, bars, when,
                      "tradepro_strategies.signals.mean_reversion")


def _summarise(symbol: str, strategy: str, rets: list[float], bars: list[int],
               when: list[str], rule_source: str) -> Outlook:
    k = len(rets)
    if k < MIN_TO_REPORT:
        return Outlook(
            symbol=symbol, strategy=strategy, instances=k,
            estimate_is_supported=False, rule_source=rule_source,
            first_signal=(when[0] if when else None),
            last_signal=(when[-1] if when else None),
            note=(f"only {k} historical instance(s) of this rule on {symbol} — "
                  "too few to describe. This is NOT a statement that the trade "
                  "is bad; it is a statement that this name has no record "
                  "under this rule."),
            caveats=["no estimate given — the sample cannot support one"])

    s = sorted(rets)
    supported = k >= MIN_FOR_ESTIMATE
    out = Outlook(
        symbol=symbol, strategy=strategy, instances=k,
        estimate_is_supported=supported,
        win_rate_pct=round(100 * sum(1 for x in rets if x > 0) / k, 1),
        median_pct=round(st.median(rets), 2),
        worst_pct=round(min(rets), 1), best_pct=round(max(rets), 1),
        median_hold_sessions=int(st.median(bars)),
        p10_pct=round(_pct(s, 0.10), 1), p90_pct=round(_pct(s, 0.90), 1),
        first_signal=when[0], last_signal=when[-1],
        rule_source=rule_source)

    if supported:
        out.mean_pct = round(st.fmean(rets), 2)
        out.note = (f"{k} historical instances. Half landed between "
                    f"{out.p10_pct:+.1f}% and {out.p90_pct:+.1f}% is NOT what "
                    f"this says — 80% did. Size against the spread, not the "
                    f"median.")
    else:
        # The distribution is shown; the central estimate is WITHHELD.
        out.note = (f"{k} instances — below the {MIN_FOR_ESTIMATE} needed for a "
                    f"central estimate, so no mean is given. The range is shown "
                    f"because a range of {k} observations is still informative "
                    f"about what CAN happen; an average of {k} is not.")
        out.caveats.append(
            f"mean withheld: {k} < {MIN_FOR_ESTIMATE} instances")

    out.caveats.append(
        "this is what the rule DID on this name, not a forecast. The pooled "
        "edge across all names is the evidence; a single name's record is "
        "context.")
    out.caveats.append(
        "the stop is checked on the CLOSE and does not survive a gap — the "
        "worst figure here can be exceeded")
    return out
