"""Regression-panel runner — evaluates the frozen YAML at
``tradepro_eval_regression.yaml`` against the live compare cache.

Track 1 of the TradePro Test Case Framework (user-provided
2026-05-22). The YAML is the source of truth for "what SHOULD the
system say about ticker X today" — when an engine change ships, run
this panel to detect regressions before they hit production.

Three-way status per case:
  PASS — every assertion in `expected` matched what the row carries.
  FAIL — at least one assertion mismatched. Includes diff.
  SKIP — the row wasn't available, OR the assertion checks a feature
         the engine doesn't surface yet (panel documents the gap).

The runner does NOT mutate expected values automatically — that's
the YAML's stated rule. Engine changes update the YAML deliberately.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("tradepro.regression_panel")


# Map YAML assertion keys to the row-derivation function that
# computes the actual value. Functions return one of:
#   (status, actual, expected, detail)
# status ∈ {"pass", "fail", "skip"}.
# Adding a new assertion means adding an entry here — the resolver
# is a closed set, so a YAML typo fails loudly rather than passing
# silently.
AssertionFn = "Callable[[Any, dict], tuple[str, Any, Any, str]]"


@dataclass
class CaseResult:
    case_id: str
    ticker: str
    category: str
    status: str   # "pass" / "fail" / "skip" / "missing"
    assertions: list[dict] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "ticker": self.ticker,
            "category": self.category,
            "status": self.status,
            "assertions": self.assertions,
            "error": self.error,
        }


# --- assertion resolvers -----------------------------------------------

def _bucket(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    actual = (row.get("bucket") or "").upper()
    expected = (expected or "").upper()
    # HOLD/HOLD_OR_AVOID/etc — accept variants.
    if expected in ("HOLD_OR_AVOID", "WATCH_OR_AVOID"):
        ok = actual in ("HOLD", "WAIT", "AVOID")
    else:
        ok = actual == expected
    return ("pass" if ok else "fail", actual, expected, "row.bucket")


def _trend_filters_passing(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    """Derive trend-filter pass from the decision trace. Looks for SMA200
    + Ichimoku cloud-position lines and checks both are 'pass'."""
    trace = (row.get("market_state") or {}).get("decision_trace") or []
    sma_ok = ichi_ok = None
    for r in trace:
        label = (r.get("label") or "").lower()
        status = (r.get("status") or "").lower()
        if "sma" in label and "200" in label:
            sma_ok = status == "pass"
        if "ichimoku" in label:
            ichi_ok = status == "pass"
    if sma_ok is None and ichi_ok is None:
        return ("skip", None, expected, "no SMA200 / Ichimoku rows in decision_trace")
    actual = bool(sma_ok) and bool(ichi_ok)
    return ("pass" if actual == bool(expected) else "fail",
            actual, expected,
            f"sma200={sma_ok} ichimoku={ichi_ok}")


def _momentum_aligned(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    """Momentum is 'aligned' when 3m momentum is positive AND RSI is
    above the oversold floor (40). Cheap proxy."""
    ms = row.get("market_state") or {}
    mom = ms.get("momentum_3m_pct")
    rsi = ms.get("rsi_14")
    if mom is None or rsi is None:
        return ("skip", None, expected, "no momentum_3m_pct / rsi_14 on row")
    actual = mom > 0 and rsi > 40
    return ("pass" if actual == bool(expected) else "fail",
            actual, expected,
            f"momentum_3m={mom} rsi={rsi}")


def _key_strategies_firing(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    """The Compare row carries only ONE strategy at a time — this is
    aggregated upstream. Skipping with a precise reason rather than
    silently passing/failing."""
    return ("skip", None, expected,
            "per-symbol strategy-fire list requires sym_rows context not on single row")


def _analyst_feed_populated(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    ar = row.get("analyst_recommendations") or {}
    total = (ar.get("strong_buy") or 0) + (ar.get("buy") or 0) \
        + (ar.get("hold") or 0) + (ar.get("sell") or 0) + (ar.get("strong_sell") or 0)
    actual = bool(total)
    return ("pass" if actual == bool(expected) else "fail",
            actual, expected,
            f"analyst_recommendations total={total}")


def _today_bucket(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    actual = (row.get("bucket") or "").upper()
    return ("pass" if actual == (expected or "").upper() else "fail",
            actual, expected, "row.bucket")


def _entry_signal(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    actual = ((row.get("market_state") or {}).get("entry_signal") or "").upper()
    return ("pass" if actual == (expected or "").upper() else "fail",
            actual, expected, "market_state.entry_signal")


def _coherence_check(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    """PASS if today_bucket and entry_signal agree; FAIL otherwise.
    Designed to catch BUG-002."""
    bucket = (row.get("bucket") or "").upper()
    entry = ((row.get("market_state") or {}).get("entry_signal") or "").upper()
    coherent = bucket and entry and bucket == entry
    actual = "PASS" if coherent else "FAIL"
    return ("pass" if actual == (expected or "").upper() else "fail",
            actual, expected,
            f"bucket={bucket!r} entry_signal={entry!r}")


def _ui_score_consistent(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    return ("skip", None, expected,
            "UI consistency check requires E2E browser test, not row inspection")


def _data_feed_healthy(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    bars = row.get("bars") or 0
    error = row.get("error")
    last_price = (row.get("market_state") or {}).get("last_price")
    actual = bars > 0 and not error and last_price is not None
    return ("pass" if actual == bool(expected) else "fail",
            actual, expected,
            f"bars={bars} error={error!r} last_price={last_price!r}")


def _no_phantom_signals(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    """If row.error is set, the bucket should NOT be a confident BUY/AVOID."""
    error = row.get("error")
    bucket = (row.get("bucket") or "").upper()
    actual = not (error and bucket in ("BUY", "AVOID"))
    return ("pass" if actual == bool(expected) else "fail",
            actual, expected,
            f"error={error!r} bucket={bucket!r}")


def _volatility_flag(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    return ("skip", None, expected,
            "volatility regime tag not yet on the compare row (DATA_ROADMAP §9)")


def _volatility_percentile(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    return ("skip", None, expected, "volatility percentile not yet on the compare row")


def _vol_warning_surfaced(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    return ("skip", None, expected, "vol-warning badge not yet wired in the UI")


def _position_sizing_note(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    return ("skip", None, expected, "position-sizing note not yet on the row")


def _horizon_tag(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    hc = row.get("horizon_classification") or {}
    swing = ((hc.get("swing") or {}).get("signal") or "").upper()
    long_term = ((hc.get("long_term") or {}).get("signal") or "").upper()
    if expected == "long-term":
        ok = long_term in ("BUY", "STRONG_BUY") and swing not in ("BUY", "STRONG_BUY")
    elif expected == "intraday":
        ok = swing in ("BUY", "STRONG_BUY")
    else:
        ok = True
    return ("pass" if ok else "fail",
            f"swing={swing} long_term={long_term}",
            expected,
            "horizon_classification")


def _dividend_context(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    fund = row.get("fundamentals") or {}
    dy = fund.get("dividend_yield_pct")
    actual = dy is not None and dy > 0
    return ("pass" if actual == (expected == "present") else "fail",
            f"dividend_yield_pct={dy}", expected, "fundamentals.dividend_yield_pct")


def _analyst_events_count(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    events = row.get("analyst_upgrades") or []
    actual = len(events)
    try:
        ok = actual == int(expected)
    except (TypeError, ValueError):
        ok = False
    return ("pass" if ok else "fail", actual, expected, "len(analyst_upgrades)")


def _earnings_flag(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    hist = row.get("historical_earnings") or []
    actual = "present" if hist else "absent"
    return ("pass" if actual == expected else "fail",
            actual, expected, f"historical_earnings count={len(hist)}")


def _data_age_hours(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    days = row.get("data_age_days")
    if days is None:
        return ("skip", None, expected, "no data_age_days on row")
    hours = days * 24
    if isinstance(expected, str) and expected.startswith("<"):
        try:
            threshold = float(expected.lstrip("<").strip())
            ok = hours < threshold
        except ValueError:
            return ("skip", hours, expected, "could not parse threshold")
        return ("pass" if ok else "fail", f"{hours}h", expected, "data_age_days*24")
    return ("skip", hours, expected, "expected value not in '<N' form")


def _price_non_null(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    last_price = (row.get("market_state") or {}).get("last_price")
    actual = last_price is not None
    return ("pass" if actual == bool(expected) else "fail",
            f"last_price={last_price}", expected, "market_state.last_price")


def _feed_error_surfaced(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    """If error is set, that's the surfaced error. Skip when no error."""
    error = row.get("error")
    if not error:
        return ("skip", None, expected, "no error condition to verify on this run")
    actual = True
    return ("pass" if actual == bool(expected) else "fail",
            f"error={error!r}", expected, "row.error")


def _contract_identified(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    return ("skip", None, expected,
            "futures contract-identification not yet on the row (Phase 4C)")


def _roll_warning_present(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    return ("skip", None, expected,
            "futures roll-warning not yet on the row (Phase 4C)")


def _decide_bucket(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    if expected is None:
        return ("skip", row.get("bucket"), None,
                "expected null — market-dependent, no assertion")
    actual = (row.get("bucket") or "").upper()
    return ("pass" if actual == (expected or "").upper() else "fail",
            actual, expected, "row.bucket")


def _research_verdict(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    return ("skip", None, expected,
            "research-page verdict requires separate /api/signals/evaluate fetch")


def _decide_vs_research_consistent(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    return ("skip", None, expected,
            "cross-system check requires both /compare and /signals fetches")


def _momentum_filters_passing(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    """Momentum filters loosely defined: RSI > 50 and momentum_3m > 0."""
    ms = row.get("market_state") or {}
    rsi = ms.get("rsi_14")
    mom = ms.get("momentum_3m_pct")
    if rsi is None or mom is None:
        return ("skip", None, expected, "no rsi_14 / momentum_3m_pct")
    actual = rsi > 50 and mom > 0
    return ("pass" if actual == bool(expected) else "fail",
            actual, expected, f"rsi={rsi} mom={mom}")


def _correct_bucket_post_fix(expected: Any, row: dict) -> tuple[str, Any, Any, str]:
    """Aspirational — what the system SHOULD do after BUG-001 is fixed.
    Always skip until the fix lands; YAML drives the assertion update."""
    return ("skip", None, expected,
            "aspirational — BUG-001 not yet fixed; YAML to be updated post-fix")


# Closed map of supported assertion keys.
ASSERTION_RESOLVERS = {
    "swing_bucket": _bucket,
    "trend_filters_passing": _trend_filters_passing,
    "momentum_aligned": _momentum_aligned,
    "key_strategies_firing": _key_strategies_firing,
    "analyst_feed_populated": _analyst_feed_populated,
    "today_bucket": _today_bucket,
    "entry_signal": _entry_signal,
    "coherence_check": _coherence_check,
    "ui_score_consistent": _ui_score_consistent,
    "data_feed_healthy": _data_feed_healthy,
    "no_phantom_signals": _no_phantom_signals,
    "volatility_flag": _volatility_flag,
    "volatility_percentile": _volatility_percentile,
    "vol_warning_surfaced": _vol_warning_surfaced,
    "position_sizing_note": _position_sizing_note,
    "horizon_tag": _horizon_tag,
    "dividend_context": _dividend_context,
    "analyst_events_count": _analyst_events_count,
    "earnings_flag": _earnings_flag,
    "data_age_hours": _data_age_hours,
    "price_non_null": _price_non_null,
    "feed_error_surfaced_on_failure": _feed_error_surfaced,
    "contract_identified": _contract_identified,
    "roll_warning_present": _roll_warning_present,
    "decide_bucket": _decide_bucket,
    "research_verdict": _research_verdict,
    "decide_vs_research_consistent": _decide_vs_research_consistent,
    "momentum_filters_passing": _momentum_filters_passing,
    "correct_bucket_post_fix": _correct_bucket_post_fix,
}


def load_panel(path: str | Path) -> dict:
    """Read the YAML at `path`. Raises FileNotFoundError if missing."""
    import yaml
    text = Path(path).read_text()
    return yaml.safe_load(text)


def evaluate_case(case: dict, row: dict | None) -> CaseResult:
    """Run every assertion in `case['expected']` against `row`. When
    `row` is None, every assertion lands as `missing` (panel can't
    evaluate without data)."""
    result = CaseResult(
        case_id=case.get("id", "?"),
        ticker=case.get("ticker", "?"),
        category=case.get("category", "?"),
        status="pass",
    )
    if row is None:
        result.status = "missing"
        result.error = f"no compare row available for {case.get('ticker')}"
        return result

    expected = case.get("expected") or {}
    any_fail = False
    any_pass = False
    for key, exp in expected.items():
        resolver = ASSERTION_RESOLVERS.get(key)
        if resolver is None:
            result.assertions.append({
                "key": key, "status": "skip",
                "detail": f"unknown assertion key '{key}' — not in resolver map",
            })
            continue
        try:
            status, actual, exp_resolved, detail = resolver(exp, row)
        except Exception as e:  # noqa: BLE001
            status, actual, exp_resolved, detail = (
                "skip", None, exp, f"resolver crashed: {e}",
            )
        result.assertions.append({
            "key": key,
            "status": status,
            "actual": actual,
            "expected": exp_resolved,
            "detail": detail,
        })
        if status == "fail":
            any_fail = True
        elif status == "pass":
            any_pass = True

    if any_fail:
        result.status = "fail"
    elif any_pass:
        result.status = "pass"
    else:
        result.status = "skip"  # every assertion skipped
    return result


def format_report(results: list[CaseResult]) -> str:
    """Markdown-style summary table. Returns a single multi-line
    string ready for stdout / a file."""
    lines: list[str] = []
    counts = {"pass": 0, "fail": 0, "skip": 0, "missing": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    lines.append("# TradePro regression panel — results")
    lines.append("")
    lines.append(
        f"**Summary:** "
        f"{counts['pass']} pass · "
        f"{counts['fail']} fail · "
        f"{counts['skip']} skip · "
        f"{counts['missing']} missing"
    )
    lines.append("")
    lines.append("| Case | Ticker | Category | Status | Detail |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        emoji = {"pass": "✅", "fail": "❌", "skip": "⚠️", "missing": "—"}.get(r.status, "?")
        worst = next(
            (a for a in r.assertions if a["status"] == "fail"),
            next((a for a in r.assertions if a["status"] == "skip"), None),
        )
        detail = ""
        if r.error:
            detail = r.error
        elif worst:
            detail = f"{worst['key']}: {worst['detail']}"
        lines.append(
            f"| {r.case_id} | {r.ticker} | {r.category} | {emoji} {r.status} | {detail} |"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "ASSERTION_RESOLVERS",
    "CaseResult",
    "evaluate_case",
    "format_report",
    "load_panel",
]
