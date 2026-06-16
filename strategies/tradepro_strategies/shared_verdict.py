"""Canonical BUY/WAIT/AVOID verdict logic — the single home for the
verdict pipeline so every surface computes the same way (kills the
cross-surface contradictions like TCS BUY-in-panel vs AVOID-in-table).

These pure functions were extracted verbatim from compare.py (behaviour
preserved; pinned by features/{conviction,sentiment_demotion,
earnings_suppressor}.feature + tests/test_shared_verdict_parity.py).
compare._attach_bucket_and_rationale remains the canonical ORCHESTRATOR
that chains them; on-demand callers (mcp/tools.py) call the individual
functions. compare.py re-exports every name below for backward compat.

Threshold constants live at the top so they are single-sourced and
visible rather than buried as magic default-args.
"""
from __future__ import annotations

# --- Canonical verdict thresholds (the pipeline's OWN knobs) ----------- #
VOLUME_CONFIRM_RATIO = 1.2        # vol_ratio_20d ≥ this → HIGH conviction
EARNINGS_SUPPRESS_DAYS = 7        # BUY suppressed if earnings within N days
SENTIMENT_WAIT_MEAN = -0.30       # BUY→WAIT when 7d mean ≤ this …
SENTIMENT_WAIT_MIN_NEG = 2        # … AND ≥ this many material-negative
SENTIMENT_AVOID_MEAN = -0.45      # any→AVOID when mean ≤ this …
SENTIMENT_AVOID_MIN_NEG = 3       # … AND ≥ this many material-negative
SWING_STRICT_MEAN = -0.05         # swing-strict BUY→WAIT below this mean
SWING_STRICT_NEUTRAL_BAND = 0.10  # neutral band for the 1-material-neg rule
EXTREME_RANGE_PCT = 85.0          # range-position cap for the BUY→WAIT veto

def compute_bucket(
    *,
    price_verdict: str,
    price_reason: str | None,
    long_count: int,
    total: int,
) -> tuple[str, str]:
    """Pure helper: roll the now-or-wait verdict + the per-strategy
    long/flat votes into a single bucket (BUY/WAIT/AVOID) and a
    one-line reason. Sentiment demotion is layered on by callers that
    have news data; this helper stays sentiment-free so on-demand
    paths (the MCP `evaluate_symbols` tool) can use it without paying
    the news-fetching latency.

    BUY requires price_verdict == "BUY" — when market_state has
    decided HOLD/WAIT/AVOID, those carry through regardless of how
    many strategies are still long. The earlier "HOLD + majority
    long → BUY" promotion conflated "already in position" with
    "good time to add" and was responsible for the MTUM / VLUE /
    QUAL / USMV class of contradictions (bucket=BUY while the same
    row's entry_signal=HOLD at 96-100th-pctile of 52w range).

    A confident BUY also requires majority-strategy long — otherwise
    only one or two strategies have an edge here and we WAIT for
    broader confirmation.
    """
    majority_long = long_count > total / 2 if total > 0 else False
    if price_verdict == "AVOID":
        return "AVOID", price_reason or "Confirmed downtrend."
    if price_verdict == "WAIT":
        return "WAIT", price_reason or "Better entries likely soon."
    if price_verdict == "BUY":
        if majority_long:
            return (
                "BUY",
                price_reason
                or f"{long_count} of {total} strategies currently long; "
                   f"price action supports entry.",
            )
        return (
            "WAIT",
            f"Price-action gate passes but only {long_count} of {total} "
            f"strategies are long — wait for broader confirmation.",
        )
    # price_verdict == "HOLD" (or anything else we didn't model) →
    # never BUY. HOLD means "no fresh entry edge per market_state".
    # If you're already long, the per-strategy in_position state
    # tells you that; the bucket should not say BUY.
    if majority_long:
        consensus = f"{long_count} of {total} strategies currently long"
        if price_reason:
            return ("WAIT", f"{consensus} but {price_reason} — no fresh entry edge.")
        return ("WAIT", f"{consensus} but no fresh entry edge per market_state.")
    return (
        "WAIT",
        f"Only {long_count} of {total} strategies are currently long "
        f"— wait for more confirmation.",
    )


def compute_conviction(
    *,
    bucket: str,
    market_state: dict,
    sentiment_demoted: bool,
    horizon_demoted: bool,
) -> tuple[str, str]:
    """Three-tier conviction classification per
    IMPROVEMENT_SUGGESTIONS_v1.md §1.3 — the safety net the spec adds
    on top of the bucket pipeline. Returns (conviction, reason).

    Decision tree (first match wins):
      - trend filters failing → LOW (regardless of bucket; the BUG-001
        belt-and-braces — even if compute_bucket regresses, a BUY on a
        broken trend gets demoted to WATCH downstream).
      - sentiment or horizon demotion fired → MEDIUM (the system
        adjusted away from the raw price signal; conviction follows).
      - bucket = BUY with volume confirmation → HIGH.
      - bucket = BUY without volume confirmation → MEDIUM.
      - bucket = WAIT/AVOID and trend ok → MEDIUM (these aren't entry
        recommendations, but conviction in the *avoid* call is still
        normal-confidence when filters agree).

    Trend filters: pass when above_sma_200 AND
    ichimoku_cloud_position != BELOW_CLOUD. Missing data fails open
    (treat as trend OK) so we don't punish symbols whose ichimoku
    series hasn't computed yet — the bucket pipeline itself catches
    the actual price-below-SMA200 case via the existing trend gate
    (task #70).

    Volume confirmation: volume_ratio_20d >= 1.2 (a fifth above the
    20-day average). Missing → treated as "not confirmed" so we cap
    at MEDIUM rather than promoting to HIGH on thin data.
    """
    above_sma_200 = market_state.get("above_sma_200")
    ichi_pos = (market_state.get("ichimoku_cloud_position") or "").upper()
    # Only fail trend when we have evidence either way; missing data
    # is not the same as "trend broken".
    sma_breaks_trend = above_sma_200 is False
    ichi_breaks_trend = ichi_pos == "BELOW_CLOUD"
    trend_broken = sma_breaks_trend or ichi_breaks_trend
    if trend_broken:
        bits = []
        if sma_breaks_trend:
            bits.append("price below 200d SMA")
        if ichi_breaks_trend:
            bits.append("below Ichimoku cloud")
        return ("LOW", f"Trend filters failing: {' + '.join(bits)}.")
    if sentiment_demoted:
        return ("MEDIUM", "Sentiment demotion fired — verdict moved off raw price signal.")
    if horizon_demoted:
        return ("MEDIUM", "Horizon / range demotion fired — verdict adjusted by horizon view.")
    if bucket == "BUY":
        vol_ratio = market_state.get("volume_ratio_20d")
        try:
            confirmed = vol_ratio is not None and float(vol_ratio) >= VOLUME_CONFIRM_RATIO
        except (TypeError, ValueError):
            confirmed = False
        if confirmed:
            return ("HIGH", f"Trend + consensus + volume confirm (vol_ratio={vol_ratio:.2f}).")
        return ("MEDIUM", "Trend + consensus agree; volume confirmation absent or thin.")
    return ("MEDIUM", "Trend filters pass; bucket is WAIT/AVOID so no entry recommendation.")


def cap_bucket_at_low_conviction(
    *,
    bucket: str,
    reason: str,
    conviction: str,
) -> tuple[str, str, bool]:
    """If conviction == LOW and bucket would otherwise read as a BUY,
    demote bucket → WAIT. Per IMPROVEMENT_SUGGESTIONS_v1.md §1.3, LOW
    conviction means "WATCH only — no entry recommendation". Returns
    (bucket, reason, demoted).

    Pure function — no row mutation. Tested directly in
    features/conviction.feature so the contract is auditable
    independent of the wider compare flow.
    """
    if conviction == "LOW" and bucket == "BUY":
        return (
            "WAIT",
            ("BUG-001 conviction veto: trend filters failing, bucket "
             "capped at WAIT (was BUY). Original reason: " + (reason or "—")),
            True,
        )
    return bucket, reason, False


def apply_earnings_suppressor(
    *,
    bucket: str,
    reason: str,
    conviction: str,
    days_until_earnings: int | None,
    threshold_days: int = EARNINGS_SUPPRESS_DAYS,
) -> tuple[str, str, str, bool]:
    """Suppress entry recommendations on swings into earnings.

    Per IMPROVEMENT_SUGGESTIONS_v1.md §2.2 + SIGNAL_CARD_SPEC_v1.md §2.2:
    when an earnings announcement lands within `threshold_days`, a
    swing BUY isn't actionable — the post-print gap can swallow the
    entire reward leg of a 1:2 setup. We don't try to predict the
    beat/miss; we just refuse to call BUY into the event window.

    Returns (bucket, reason, conviction, suppressed). Demotes:
      - bucket BUY → WAIT (no entry recommendation)
      - conviction HIGH → MEDIUM (only HIGH gets demoted; LOW stays LOW)

    Pure function — no row mutation. Tested directly in
    features/earnings_suppressor.feature so the threshold semantics
    stay auditable.
    """
    if days_until_earnings is None:
        return bucket, reason, conviction, False
    try:
        days = int(days_until_earnings)
    except (TypeError, ValueError):
        return bucket, reason, conviction, False
    if days < 0 or days > threshold_days:
        return bucket, reason, conviction, False

    new_bucket = "WAIT" if bucket == "BUY" else bucket
    new_conviction = "MEDIUM" if conviction == "HIGH" else conviction
    if new_bucket == bucket and new_conviction == conviction:
        # Nothing changed — bucket was already WAIT / AVOID and
        # conviction wasn't HIGH. Still mark suppressed so the UI can
        # surface the WARNING flag.
        return bucket, reason, conviction, True

    suppress_note = (
        f"Earnings suppression: earnings in {days}d (threshold "
        f"{threshold_days}d). Original verdict: {bucket}. Post-print gap "
        "risk swamps the reward leg of a 1:2 setup."
    )
    return new_bucket, suppress_note, new_conviction, True


def enforce_coherence(
    row: dict,
    *,
    bucket: str,
    sentiment_demoted: bool,
    horizon_demoted: bool,
) -> None:
    """Mutate `row` in-place so `market_state.entry_signal` agrees
    with the final `bucket`, and surface a top-level `coherence`
    block. The raw price-action signal is preserved as
    `market_state.raw_entry_signal` for the decision trace.

    BUG-002 surface fix per IMPROVEMENT_SUGGESTIONS_v1.md §1.3 + §4:
    on every shipped row the two fields are equal by construction
    (the panel's `coherence_check` resolver compares them directly),
    while the `coherence.supersede_reason` field labels *why* the
    raw signal was overridden — sentiment_demotion, horizon_demotion,
    or consensus_or_factor_fit when neither of those fired but the
    bucket vote still moved away from the raw price verdict (e.g.
    not enough strategies are long to promote HOLD→BUY).
    """
    ms_dict = row.get("market_state") or {}
    raw_entry_sig = ms_dict.get("entry_signal")
    supersede_reason: str | None
    if raw_entry_sig and raw_entry_sig != bucket:
        if sentiment_demoted:
            supersede_reason = "sentiment_demotion"
        elif horizon_demoted:
            supersede_reason = "horizon_demotion"
        else:
            supersede_reason = "consensus_or_factor_fit"
        ms_dict["raw_entry_signal"] = raw_entry_sig
        ms_dict["entry_signal"] = bucket
        ms_dict["entry_signal_superseded_by"] = bucket
        ms_dict["entry_signal_note"] = (
            f"Raw price signal was {raw_entry_sig}; final verdict "
            f"is {bucket} (reason: {supersede_reason})."
        )
        row["market_state"] = ms_dict
    else:
        supersede_reason = None
    final_entry_sig = ms_dict.get("entry_signal", bucket)
    row["coherence"] = {
        "today_bucket": bucket,
        "entry_signal": final_entry_sig,
        "raw_entry_signal": ms_dict.get("raw_entry_signal", raw_entry_sig),
        "consistent": final_entry_sig == bucket,
        "supersede_reason": supersede_reason,
    }


def apply_swing_strict_demotion(
    *,
    bucket: str,
    reason: str,
    mean: float | None,
    material_negative_count: int | None,
    strict_mean_threshold: float = SWING_STRICT_MEAN,
    neutral_band: float = SWING_STRICT_NEUTRAL_BAND,
) -> tuple[str, str, bool]:
    """Tighter overlay for medium-long-term holds. Standard demotion
    (apply_sentiment_demotion) is calibrated for intraday — mean ≤ -0.30
    + 2 material negatives. For a 1-8w swing or longer hold the trader
    carries news exposure through every cycle, so the threshold must be
    stricter: any single material-negative headline AND sentiment in
    the neutral-to-slightly-negative band warrants WAIT.

    Fires AFTER apply_sentiment_demotion so the AVOID and standard WAIT
    cases are already handled. This is the "TSLA squeaks through at
    +0.02 mean with 1 material negative on SpaceX merger uncertainty"
    case — fine for intraday, wrong for medium-long-term swing.

    Two rules (BUY → WAIT only — never escalates to AVOID):
      A: mean ≤ -0.05 (mildly negative or below) → demote even with no
         material negatives. The market mood is tilting bearish; don't
         add at this level for a multi-week hold.
      B: mean in (-0.10, +0.10) (neutral band) AND mat_neg ≥ 1 → demote.
         The news isn't outright bearish but it's mixed enough that
         one material negative tips the calculus.
    """
    if bucket != "BUY":
        return bucket, reason, False
    if mean is None:
        return bucket, reason, False
    mat_neg = material_negative_count or 0
    # Rule A — mildly-or-more negative mean.
    if mean <= strict_mean_threshold:
        return (
            "WAIT",
            (f"Swing-strict demotion: 7d mean {mean:.2f} ≤ "
             f"{strict_mean_threshold} — mildly negative news backdrop, "
             f"tighter threshold for medium-long-term hold."),
            True,
        )
    # Rule B — neutral band AND material negatives present.
    if abs(mean) <= neutral_band and mat_neg >= 1:
        return (
            "WAIT",
            (f"Swing-strict demotion: 7d mean {mean:.2f} within neutral "
             f"band ±{neutral_band} AND {mat_neg} material-negative "
             f"headline(s) — news flow noisy for a medium-long-term "
             f"entry; wait for clearer backdrop."),
            True,
        )
    return bucket, reason, False


def apply_sentiment_demotion(
    *,
    bucket: str,
    reason: str,
    mean: float | None,
    material_negative_count: int | None,
    mean_threshold: float = SENTIMENT_WAIT_MEAN,
    min_material: int = SENTIMENT_WAIT_MIN_NEG,
    avoid_mean_threshold: float = SENTIMENT_AVOID_MEAN,
    avoid_min_material: int = SENTIMENT_AVOID_MIN_NEG,
) -> tuple[str, str, bool]:
    """Two-tier sentiment demotion. Returns (bucket, reason, demoted).

    Tier 1 — STRONGER (any → AVOID): mean ≤ -0.45 AND ≥3 material-
    negative headlines. News flow is materially worse than a routine
    WAIT — separates "negative backdrop" (Tier 2) from "genuinely
    hostile" (Tier 1). Fires regardless of starting bucket so a BUY
    or WAIT both land in AVOID when the news is this bad.

    Tier 2 — STANDARD (BUY → WAIT): mean ≤ -0.30 AND ≥2 material-
    negative headlines. The original demotion rule kept for backwards
    compatibility — flagging a BUY when sentiment is bad enough to
    warrant sitting out, but not bad enough to call AVOID.

    Pure function — no side effects, no row mutation. Tested
    directly in features/sentiment_demotion.feature so each tier's
    behaviour is auditable independent of the wider compare flow.
    """
    mat_neg = material_negative_count or 0
    if (mean is not None
            and mean <= avoid_mean_threshold
            and mat_neg >= avoid_min_material
            and bucket != "AVOID"):
        return (
            "AVOID",
            (f"Sentiment demotion to AVOID: 7d mean {mean:.2f} ≤ "
             f"{avoid_mean_threshold} AND {mat_neg} material-negative "
             f"headlines (≥ {avoid_min_material}) — news flow is "
             f"materially worse than a routine WAIT."),
            True,
        )
    if bucket == "BUY":
        if (mean is not None
                and mean <= mean_threshold
                and mat_neg >= min_material):
            return (
                "WAIT",
                (f"Sentiment demotion: 7d mean {mean:.2f} ≤ "
                 f"threshold {mean_threshold} AND {mat_neg} "
                 f"material-negative headlines (≥ {min_material})."),
                True,
            )
    return bucket, reason, False


def apply_horizon_and_range_demotion(
    *,
    bucket: str,
    reason: str,
    horizon_classification: dict | None,
    range_pct: float | None,
    extreme_range_threshold: float = EXTREME_RANGE_PCT,
) -> tuple[str, str, bool]:
    """Demote a BUY to WAIT when the entry-timing risk is bad enough
    that the bucket-vote consensus shouldn't override it.

    Two veto rules:

    Rule A — Swing-horizon AVOID veto. The swing horizon (1-8w) reads
    the same data the bucket vote does, but specifically scores
    entry-timing risk (range_pct, RSI, drawdown proximity). If swing
    has decided AVOID, the row should NOT surface as BUY — that means
    the trend-followers are still long but the entry edge is gone.
    The previous logic (HOLD + majority-long → BUY) ignored this and
    surfaced BUY on QUAL/USMV at the 100th-percentile of their 52w
    range. This rule downgrades that to WAIT.

    Rule B — Extreme range-pct cap. range_pct ≥ 95 means the price
    is sitting at the absolute top of its 52w range — a literal
    new-high zone. Buying at the high is the worst-timed entry by
    construction; downgrade BUY → WAIT independent of horizon.

    Pure function. Returns (bucket, reason, demoted_flag). Demotion
    flag lets the bucket trace surface "downgraded by horizon veto"
    as a separate line so the user can see why it didn't BUY.
    """
    if bucket != "BUY":
        return bucket, reason, False

    # Rule A — swing-horizon AVOID veto.
    if horizon_classification:
        swing = (horizon_classification.get("swing") or {})
        swing_signal = swing.get("signal")
        swing_score = swing.get("score")
        if swing_signal == "AVOID":
            score_str = f" (score {swing_score}/8)" if swing_score is not None else ""
            return (
                "WAIT",
                (f"Horizon demotion: swing horizon AVOID{score_str} — "
                 f"entry-timing edge is gone even though the multi-"
                 f"strategy consensus is still long. {reason}"),
                True,
            )

    # Rule B — extreme range-position cap, GATED on the swing horizon
    # NOT saying BUY. The unconditional version of this rule would have
    # blocked legitimate breakout BUYs like MU on the Deutsche Bank
    # upgrade (new 52w high + fresh catalyst). When the swing horizon
    # scores the row as BUY, that means the event-driven layer found
    # something — let the BUY through despite the high range_pct.
    #
    # Threshold tightened from 95 → 85 May 2026 (user bug report #7):
    # at 85th percentile the geometric risk/reward is already
    # asymmetric (3p upside vs 8p downside in the typical case) and
    # the swing-BUY exception still preserves breakouts with a real
    # catalyst — etf_factor BUYs at 96th pctile no longer pass without
    # the swing layer explicitly agreeing.
    if range_pct is not None and range_pct >= extreme_range_threshold:
        swing_signal_b = ((horizon_classification or {}).get("swing") or {}).get("signal")
        if swing_signal_b != "BUY":
            return (
                "WAIT",
                (f"Range demotion: {range_pct:.0f}th percentile of 52w range "
                 f"(≥ {extreme_range_threshold:.0f}) AND swing horizon not BUY "
                 f"— buying near the top without a fresh catalyst. {reason}"),
                True,
            )

    # Rule C — long-term BUY + swing AVOID: position-only call, not a
    # fresh swing entry. Surfaces the NVDA/AMZN class where the
    # multi-year story is intact but the entry timing is bad. Doesn't
    # change the bucket (it stays whatever Rules A/B and compute_bucket
    # produced) but enriches the reason so the user sees the split.
    if horizon_classification:
        swing_signal_c = (horizon_classification.get("swing") or {}).get("signal")
        long_signal_c = (horizon_classification.get("long_term") or {}).get("signal")
        if long_signal_c == "BUY" and swing_signal_c == "AVOID":
            return (
                bucket,
                (f"{reason} "
                 f"Long-term horizon = BUY but swing horizon = AVOID: "
                 f"strong multi-year hold candidate, NOT a fresh swing "
                 f"entry today."),
                False,
            )

    # Rule D — passive-only BUY guard. If passive horizon (3-5yr DCA)
    # is the ONLY horizon saying BUY and neither swing (1-8w) nor
    # long-term (6-18m) confirms, the row shouldn't surface as today's
    # action — the DCA thesis is "buy a little, regularly", not "buy
    # the whole position at today's open". User Bug #10. Demote BUY to
    # WAIT with an explicit DCA framing so the user gets routed to
    # the right mental model.
    if horizon_classification:
        swing_d = (horizon_classification.get("swing") or {}).get("signal")
        long_d = (horizon_classification.get("long_term") or {}).get("signal")
        passive_d = (horizon_classification.get("passive") or {}).get("signal")
        if (
            passive_d == "BUY"
            and swing_d not in ("BUY",)
            and long_d not in ("BUY",)
        ):
            return (
                "WAIT",
                (f"Passive horizon = BUY (good DCA candidate) but neither "
                 f"swing nor long-term horizons confirm — this is a regular-"
                 f"contribution thesis, not a same-day full-position entry. "
                 f"{reason}"),
                True,
            )

    return bucket, reason, False
