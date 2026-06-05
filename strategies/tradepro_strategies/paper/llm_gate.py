"""LLMSignalGate — pluggable LLM approval layer for paper trading signals.

Sits between signal generation and order emission. For every signal the
strategy produces, the gate:
  1. Fetches recent news headlines for the symbol (via news.py)
  2. Scores headlines with the configured LLM (via news_sentiment.py)
  3. Computes an aggregate sentiment score (-1.0 to +1.0)
  4. Returns a GateDecision: APPROVED / VETOED / APPROVED_BOOSTED

Configuration (all fields configurable via UI / MCP):
  enabled              bool    True = gate active; False = always approve
  provider_purpose     str     LLM purpose tag (default "sentiment")
  sentiment_veto_below float   Veto when aggregate sentiment < this (default -0.4)
  sentiment_boost_above float  Boost when aggregate sentiment > this (default +0.5)
  boost_multiplier     float   Size multiplier on boost (default 1.25)
  max_headlines        int     Headlines to fetch per symbol (default 5)
  news_lookback_hours  int     Ignore news older than this (default 24)
  min_material_for_veto int    Minimum material articles before veto fires (default 1)
  fail_open            bool    True = approve on LLM error (default True, never block on failure)

Design notes:
- fail_open=True means trading is NEVER blocked by LLM failure. The gate
  is advisory, not a hard dependency.
- No network calls in __init__ — all I/O is in evaluate().
- _news_fn and _score_fn are injectable for tests.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class LLMGateConfig:
    """Pluggable per-strategy configuration for the LLM signal gate.

    Every field is intended to be UI-tunable so a trader can dial the
    gate's aggression without a redeploy. Defaults are conservative —
    veto only on clearly-negative material news, fail_open so an LLM
    outage never blocks live trading.
    """
    enabled: bool = True
    provider_purpose: str = "sentiment"
    sentiment_veto_below: float = -0.4
    sentiment_boost_above: float = 0.5
    boost_multiplier: float = 1.25
    max_headlines: int = 5
    news_lookback_hours: int = 24
    min_material_for_veto: int = 1
    fail_open: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LLMGateConfig":
        # Tolerant: ignore unknown keys (future fields shouldn't crash
        # an older registry load), fall back to defaults for missing.
        defaults = cls()
        return cls(
            enabled=bool(d.get("enabled", defaults.enabled)),
            provider_purpose=str(d.get("provider_purpose", defaults.provider_purpose)),
            sentiment_veto_below=float(d.get("sentiment_veto_below", defaults.sentiment_veto_below)),
            sentiment_boost_above=float(d.get("sentiment_boost_above", defaults.sentiment_boost_above)),
            boost_multiplier=float(d.get("boost_multiplier", defaults.boost_multiplier)),
            max_headlines=int(d.get("max_headlines", defaults.max_headlines)),
            news_lookback_hours=int(d.get("news_lookback_hours", defaults.news_lookback_hours)),
            min_material_for_veto=int(d.get("min_material_for_veto", defaults.min_material_for_veto)),
            fail_open=bool(d.get("fail_open", defaults.fail_open)),
        )


# ---------------------------------------------------------------------------
# Decision result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalystHint:
    """Compact projection of a catalyst row for the gate's
    consumption. Mirrors the .NET CatalystSummary shape (Phase C-3)
    so callers can hand the gate whatever they fetched from
    ``/api/catalysts`` without reshaping.

    Defining a Python-side mirror (rather than reusing a richer
    dataclass elsewhere) keeps `llm_gate` import-cheap — the gate
    runs inside every paper strategy tick and we don't want it
    dragging in the comparator graph."""
    id: int
    kind: str
    occurs_on: str | None      # YYYY-MM-DD or None when undated
    severity: str              # "low" | "medium" | "high"
    title: str = ""
    source: str = ""
    days_until: int | None = None


@dataclass(frozen=True)
class GateDecision:
    """Outcome of a single LLMSignalGate.evaluate() call.

    Frozen so the strategy can pass it through to the audit log without
    worrying about downstream mutation.

    Phase C-3.1 additions:
      * ``catalyst_flag`` mirrors the .NET enricher's flag
        (``tech_event_divergence`` / ``tech_event_alignment`` /
        ``None``). Stamped on the audit row so Phase H replay can
        attribute boosts/dampens to the catalyst overlay.
      * ``catalyst_ids`` records the registry IDs the gate consulted.
        Phase H matches against this to reconstruct the gate's view
        of the world.
    """
    action: str
    scale_factor: float
    reason: str
    sentiment_score: float | None = None
    headlines_checked: int = 0
    provider_used: str = ""
    catalyst_flag: str | None = None
    catalyst_ids: tuple[int, ...] = ()

    APPROVED = "APPROVED"
    VETOED = "VETOED"
    APPROVED_BOOSTED = "APPROVED_BOOSTED"

    def _replace_with_catalyst_metadata(
        self, flag: str | None, ids: tuple[int, ...],
    ) -> "GateDecision":
        """Phase C-3.1 — return a copy of this decision with the
        catalyst audit fields stamped. Used when there are catalysts
        but no flag fired (record IDs anyway so Phase H replay can
        reconstruct what the gate looked at)."""
        return GateDecision(
            action=self.action,
            scale_factor=self.scale_factor,
            reason=self.reason,
            sentiment_score=self.sentiment_score,
            headlines_checked=self.headlines_checked,
            provider_used=self.provider_used,
            catalyst_flag=flag,
            catalyst_ids=ids,
        )


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class LLMSignalGate:
    """Stateless-per-call LLM approval layer in front of order emission.

    The gate is advisory by default (fail_open=True): an LLM outage never
    blocks trading, it just produces an APPROVED decision tagged with the
    error. Use `reconfigure()` to hot-swap the config without recreating
    the strategy.

    Injectable hooks:
      _news_fn(symbol, max_items)   → list[NewsItem]
      _score_fn(items, provider)    → list[ScoredHeadline]
    Both default to the production functions in news.py / news_sentiment.py.
    Tests pass synthetic fns to avoid network calls.
    """

    def __init__(
        self,
        config: LLMGateConfig,
        *,
        _news_fn: Callable[[str, int], list] | None = None,
        _score_fn: Callable[[list, Any], list] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._config = config
        self._news_fn = _news_fn
        self._score_fn = _score_fn

    # ------------------------------------------------------------------ #
    # Configuration                                                        #
    # ------------------------------------------------------------------ #

    @property
    def config(self) -> LLMGateConfig:
        with self._lock:
            return self._config

    def reconfigure(self, new_config: LLMGateConfig) -> None:
        """Hot-swap the gate config. Thread-safe — callers in flight see
        the old config; the next call picks up the new one."""
        with self._lock:
            self._config = new_config

    # ------------------------------------------------------------------ #
    # Internals: provider + headline plumbing                              #
    # ------------------------------------------------------------------ #

    def _resolve_news_fn(self) -> Callable[[str, int], list]:
        if self._news_fn is not None:
            return self._news_fn
        # Late import — keeps __init__ network-free.
        from ..news import fetch_news

        def _fn(symbol: str, max_items: int) -> list:
            return fetch_news(symbol, limit=max_items)

        return _fn

    def _resolve_score_fn(self) -> tuple[Callable[[list, Any], list], Any, str]:
        cfg = self._config
        if self._score_fn is not None:
            # In tests the provider name may not matter; report a stub.
            return self._score_fn, None, "test"
        from ..news_sentiment import score_news
        from ..llm.factory import get_provider

        provider = get_provider(cfg.provider_purpose)
        provider_name = type(provider).__name__
        return score_news, provider, provider_name

    def _filter_recent(self, scored: list, headlines: list, lookback_hours: int) -> list:
        """Return the subset of scored items whose underlying headline
        published_at is within lookback_hours. Untimestamped items pass
        through (Yahoo's older shape often omits the field; dropping them
        would empty the sentiment for those symbols)."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        out = []
        for s, raw in zip(scored, headlines):
            pub_str = getattr(raw, "published_at", None)
            if not pub_str:
                out.append(s)
                continue
            try:
                ts = datetime.fromisoformat(str(pub_str).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                out.append(s)
                continue
            if ts >= cutoff:
                out.append(s)
        return out

    # ------------------------------------------------------------------ #
    # Catalyst overlay (Phase C-3.1)                                       #
    # ------------------------------------------------------------------ #

    # Severity + days-until thresholds for the divergence/alignment flag.
    # Match the .NET enricher (Phase C-3) so a divergence the cockpit
    # flagged is the same divergence the gate dampens — the two paths
    # never disagree about "is the catalyst imminent?".
    _IMMINENT_DAYS = 14
    _HIGH_SEVERITY = "high"

    # When the gate detects divergence (signal direction conflicts with
    # an imminent HIGH catalyst), apply a SCALE DAMPEN — NOT a veto. The
    # trader keeps agency; the gate just sizes down. Tuned to 0.5 because
    # a full veto on every imminent catalyst would suppress real trades
    # whenever earnings / FOMC sit in the next two weeks (which is most
    # of the time). The cockpit banner remains the louder signal.
    _DIVERGENCE_SCALE = 0.5

    # Alignment boost — BUY signal + imminent HIGH catalyst. Multiplies
    # the existing scale_factor (so it stacks with sentiment-boost
    # rather than overriding it).
    _ALIGNMENT_BOOST = 1.25

    @staticmethod
    def _catalyst_flag(signal: float, catalysts: list[CatalystHint] | None) -> str | None:
        """Mirror of the .NET ComputeFlag logic. Returns None /
        'tech_event_divergence' / 'tech_event_alignment'.

        Direction inference: positive `signal` (long bias) is the BUY
        analogue; zero signal is the HOLD analogue (paper engine has
        no native SELL — exits are flips of long positions). Keep
        it symmetric with the .NET enricher so cockpit + gate read
        the same room."""
        if not catalysts:
            return None
        has_imminent_high = any(
            c.severity and c.severity.lower() == LLMSignalGate._HIGH_SEVERITY
            and c.days_until is not None
            and 0 <= c.days_until <= LLMSignalGate._IMMINENT_DAYS
            for c in catalysts
        )
        if not has_imminent_high:
            return None
        if signal == 0.0:
            return "tech_event_divergence"
        if signal > 0.0:
            return "tech_event_alignment"
        # Negative signal (SELL flip) — same ambiguity as the .NET
        # side; surface chips but don't tip the flag.
        return None

    @staticmethod
    def _apply_catalyst_overlay(
        base: GateDecision,
        signal: float,
        catalysts: list[CatalystHint] | None,
    ) -> GateDecision:
        """Take whatever decision the sentiment path produced and
        layer the catalyst flag over it. Pure function — fully
        testable. The returned decision keeps the sentiment score +
        provider so the audit row reads end-to-end."""
        if not catalysts:
            return base
        flag = LLMSignalGate._catalyst_flag(signal, catalysts)
        ids = tuple(c.id for c in catalysts)
        if flag is None:
            return base._replace_with_catalyst_metadata(None, ids)

        if flag == "tech_event_divergence":
            # Dampen the scale (do not veto). Reasons gets a suffix so
            # the audit trail records WHY the size dropped.
            new_scale = base.scale_factor * LLMSignalGate._DIVERGENCE_SCALE
            reason = (
                f"{base.reason}; catalyst_divergence dampens scale "
                f"{base.scale_factor:.2f}->{new_scale:.2f}"
            )
            return GateDecision(
                action=base.action,
                scale_factor=new_scale,
                reason=reason,
                sentiment_score=base.sentiment_score,
                headlines_checked=base.headlines_checked,
                provider_used=base.provider_used,
                catalyst_flag=flag,
                catalyst_ids=ids,
            )

        if flag == "tech_event_alignment":
            # Multiply the existing scale (stacks with sentiment boost).
            new_scale = base.scale_factor * LLMSignalGate._ALIGNMENT_BOOST
            reason = (
                f"{base.reason}; catalyst_alignment boosts scale "
                f"{base.scale_factor:.2f}->{new_scale:.2f}"
            )
            # If we were already APPROVED, promote to APPROVED_BOOSTED
            # so the audit consumer treats this as a sized-up trade.
            new_action = (
                GateDecision.APPROVED_BOOSTED
                if base.action == GateDecision.APPROVED
                else base.action
            )
            return GateDecision(
                action=new_action,
                scale_factor=new_scale,
                reason=reason,
                sentiment_score=base.sentiment_score,
                headlines_checked=base.headlines_checked,
                provider_used=base.provider_used,
                catalyst_flag=flag,
                catalyst_ids=ids,
            )

        return base

    # ------------------------------------------------------------------ #
    # Public: evaluate one signal                                          #
    # ------------------------------------------------------------------ #

    def evaluate(
        self,
        symbol: str,
        signal: float,
        catalysts: list[CatalystHint] | None = None,
    ) -> GateDecision:
        """Decide whether to APPROVE / VETO / BOOST a strategy's signal.

        Never raises; on internal error returns APPROVED (with the error
        in `reason`) when fail_open=True, VETOED otherwise.

        Phase C-3.1: when ``catalysts`` is supplied, the gate layers
        the divergence/alignment overlay on top of the sentiment
        verdict. Dampens scale on divergence, boosts on alignment;
        records every catalyst ID on the audit row so Phase H replay
        can attribute the adjustment."""
        base = self._evaluate_base(symbol, signal)
        return self._apply_catalyst_overlay(base, signal, catalysts)

    def _evaluate_base(self, symbol: str, signal: float) -> GateDecision:
        """Pre-C-3.1 evaluate body. Splitting it out lets the catalyst
        overlay layer uniformly on every return path without editing
        each branch. No behaviour change vs the legacy entry point."""
        cfg = self.config

        if not cfg.enabled:
            return GateDecision(
                action=GateDecision.APPROVED,
                scale_factor=1.0,
                reason="gate disabled",
                sentiment_score=None,
                headlines_checked=0,
                provider_used="",
            )

        if signal == 0.0:
            return GateDecision(
                action=GateDecision.APPROVED,
                scale_factor=1.0,
                reason="no position to gate",
                sentiment_score=None,
                headlines_checked=0,
                provider_used="",
            )

        try:
            news_fn = self._resolve_news_fn()
            headlines = list(news_fn(symbol, cfg.max_headlines) or [])

            if not headlines:
                return GateDecision(
                    action=GateDecision.APPROVED,
                    scale_factor=1.0,
                    reason="no news — neutral",
                    sentiment_score=None,
                    headlines_checked=0,
                    provider_used="",
                )

            score_fn, provider, provider_name = self._resolve_score_fn()
            scored = list(score_fn(headlines, provider) or [])

            # Apply the lookback filter; keep parallel list of scored.
            recent_scored = self._filter_recent(scored, headlines, cfg.news_lookback_hours)

            if not recent_scored:
                return GateDecision(
                    action=GateDecision.APPROVED,
                    scale_factor=1.0,
                    reason="no news in lookback window",
                    sentiment_score=None,
                    headlines_checked=len(headlines),
                    provider_used=provider_name,
                )

            # Prefer material-only sentiments; fall back to all if none material.
            material = [s for s in recent_scored if getattr(s, "material", False) and getattr(s, "sentiment", None) is not None]
            material_count = len(material)
            if material:
                sentiments = [float(s.sentiment) for s in material]
            else:
                sentiments = [
                    float(s.sentiment)
                    for s in recent_scored
                    if getattr(s, "sentiment", None) is not None
                ]

            if not sentiments:
                return GateDecision(
                    action=GateDecision.APPROVED,
                    scale_factor=1.0,
                    reason="no scored sentiments",
                    sentiment_score=None,
                    headlines_checked=len(headlines),
                    provider_used=provider_name,
                )

            aggregate = sum(sentiments) / len(sentiments)

            if aggregate < cfg.sentiment_veto_below and material_count >= cfg.min_material_for_veto:
                return GateDecision(
                    action=GateDecision.VETOED,
                    scale_factor=0.0,
                    reason=f"sentiment {aggregate:.2f} < veto threshold {cfg.sentiment_veto_below:.2f}",
                    sentiment_score=aggregate,
                    headlines_checked=len(headlines),
                    provider_used=provider_name,
                )

            if aggregate > cfg.sentiment_boost_above:
                return GateDecision(
                    action=GateDecision.APPROVED_BOOSTED,
                    scale_factor=cfg.boost_multiplier,
                    reason=f"sentiment {aggregate:.2f} > boost threshold {cfg.sentiment_boost_above:.2f}",
                    sentiment_score=aggregate,
                    headlines_checked=len(headlines),
                    provider_used=provider_name,
                )

            return GateDecision(
                action=GateDecision.APPROVED,
                scale_factor=1.0,
                reason=f"sentiment {aggregate:.2f} within thresholds",
                sentiment_score=aggregate,
                headlines_checked=len(headlines),
                provider_used=provider_name,
            )
        except Exception as e:  # noqa: BLE001
            if cfg.fail_open:
                return GateDecision(
                    action=GateDecision.APPROVED,
                    scale_factor=1.0,
                    reason=f"llm_error: {e}",
                    sentiment_score=None,
                    headlines_checked=0,
                    provider_used="",
                )
            return GateDecision(
                action=GateDecision.VETOED,
                scale_factor=0.0,
                reason=f"llm_error (fail_closed): {e}",
                sentiment_score=None,
                headlines_checked=0,
                provider_used="",
            )


__all__ = [
    "LLMGateConfig",
    "GateDecision",
    "LLMSignalGate",
    "CatalystHint",
]
