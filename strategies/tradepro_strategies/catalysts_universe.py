"""Catalyst-sweep universe assembler.

The daily catalyst extractor runs against the UNION of:

  * **positions** — what the trader actually holds. Highest priority
    because the catalyst banner only changes a decision if it fires
    on a name we own (or are considering). For now this is supplied
    as a list to ``assemble_universe`` so the caller can plug in
    whichever source it has — IBKR live positions, IBKR paper
    positions, T212 positions, or a static seed. Once the .NET
    `/api/positions/all` endpoint stabilises across brokers the CLI
    will pull from there.

  * **watchlists** — symbols the operator is monitoring even if not
    currently holding. Surfaces catalysts before a trade so the
    LLM gate has them when the strategy emits.

  * **trader curated universe** — the trader's quant-spec ``large_50``
    list + benchmark + gold. These are the symbols the strategies
    fundamentally rotate through; we want catalyst coverage so the
    decision row always has context.

  * **curated ETF baseline** — SPY / QQQ / GLD / TLT / etc. The
    "market context" set — even if the trader doesn't hold them,
    knowing that QQQ has a high-severity catalyst tomorrow is
    relevant for any tech-heavy decision.

Dedupes case-insensitively. Strips empty / whitespace strings.
Returns the symbols in deterministic order so the cron's log is
diffable across runs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# Hand-curated baseline ETFs. The "always include" set — even if no
# strategy is currently holding them, market-context catalysts on
# these (FOMC affecting TLT, oil affecting XLE, etc.) influence the
# trader's interpretation of any other signal.
_BASELINE_ETFS: tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "DIA",                # broad benchmarks
    "GLD", "SLV",                              # precious metals
    "TLT", "IEF", "HYG", "LQD",                # rates / credit
    "XLF", "XLE", "XLK", "XLY", "XLI", "XLV",  # sector SPDRs
    "EFA", "EEM",                              # ex-US benchmarks
    "VIX",                                     # vol proxy (index)
)


# Major FX pairs we backtest. yfinance uses the ``=X`` suffix.
_BASELINE_FX: tuple[str, ...] = (
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X",
)


@dataclass(frozen=True)
class UniverseReport:
    """Audit-friendly summary of one assemble call. The cron logs
    this so an operator (and the cockpit's future "catalyst sweep
    activity" panel) can see what got covered + where each symbol
    came from."""

    symbols: tuple[str, ...]
    by_source: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.symbols)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "symbols": list(self.symbols),
            "by_source": {k: list(v) for k, v in self.by_source.items()},
        }


def _normalise(symbol: str | None) -> str | None:
    """Strip + upper. None / whitespace → None."""
    if not isinstance(symbol, str):
        return None
    s = symbol.strip()
    if not s:
        return None
    # FX (=X) and crypto (-USD) are case-sensitive in yfinance — but
    # the bar-cache resolver upper-cases everything for plugin
    # lookup anyway, so consistent upper-case here is safe.
    return s.upper()


def assemble_universe(
    *,
    positions: Iterable[str] | None = None,
    watchlists: Iterable[str] | None = None,
    trader_universe: Iterable[str] | None = None,
    include_baseline_etfs: bool = True,
    include_baseline_fx: bool = True,
    extra: Iterable[str] | None = None,
) -> UniverseReport:
    """Build the deduplicated universe + a per-source attribution
    map. Symbols are added in priority order so the ``by_source``
    dict reflects the FIRST source each symbol came from (the
    highest-priority owner of that name).

    Priority:

      1. positions (real or paper)
      2. watchlists
      3. trader_universe (large_50 + gold + benchmark)
      4. baseline_etfs
      5. baseline_fx
      6. extra
    """
    by_source: dict[str, list[str]] = {}
    seen: set[str] = set()

    def _add(source: str, raw_symbols: Iterable[str] | None) -> None:
        if not raw_symbols:
            return
        added: list[str] = []
        for raw in raw_symbols:
            sym = _normalise(raw)
            if sym is None or sym in seen:
                continue
            seen.add(sym)
            added.append(sym)
        if added:
            by_source[source] = added

    _add("positions", positions)
    _add("watchlists", watchlists)
    _add("trader_universe", trader_universe)
    if include_baseline_etfs:
        _add("baseline_etfs", _BASELINE_ETFS)
    if include_baseline_fx:
        _add("baseline_fx", _BASELINE_FX)
    _add("extra", extra)

    # Deterministic final order — by source priority first, then
    # alphabetical within each source.
    ordered: list[str] = []
    for source in ("positions", "watchlists", "trader_universe",
                   "baseline_etfs", "baseline_fx", "extra"):
        ordered.extend(sorted(by_source.get(source, [])))

    # Re-snap by_source dict to sorted lists for stable output.
    by_source_sorted = {k: tuple(sorted(v)) for k, v in by_source.items()}

    return UniverseReport(
        symbols=tuple(ordered),
        by_source=by_source_sorted,
    )


def trader_large_50() -> tuple[str, ...]:
    """Convenience accessor. Late import keeps the catalyst-universe
    module cheap — the quant_engine config is heavy enough that a
    cron that doesn't need the trader's list shouldn't pay for it."""
    from .quant_engine.config import QuantEngineConfig
    cfg = QuantEngineConfig()
    return tuple(cfg.large_50) + tuple(cfg.gold_tickers) + (cfg.benchmark,)


__all__ = [
    "UniverseReport",
    "assemble_universe",
    "trader_large_50",
]
