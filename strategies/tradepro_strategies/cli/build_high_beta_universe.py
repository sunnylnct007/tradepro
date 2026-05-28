"""tradepro-build-high-beta-universe — compute the trader's high_beta
sleeve and ingest it into Postgres as a `high_beta` universe.

End-to-end wiring of the trader's reference UniverseBuilder
(``docs/strategy.py``) into the existing TradePro universe system:

  1. Scrape the S&P 500 + S&P 400 constituent lists via the existing
     Wikipedia scraper (``universes/wikipedia.py``). One source of
     truth for "what's in the index" — refreshed daily by
     ``tradepro-refresh-universes``.

  2. Exclude crypto-beta names (COIN, MSTR, MARA, IBIT, etc.) per
     ``QuantEngineConfig.crypto_exclude``. These technically file
     with the index but the trader's spec calls them out as
     unsuitable for the equity sleeve.

  3. For each remaining candidate, ensure cached daily OHLC over the
     beta lookback window (default 252 trading days + a 50-day buffer
     for the trader's reference 50-day "minimum extra history"
     requirement). Uses the existing Parquet cache (``cache.py``) so
     repeat runs are fast.

  4. Compute 252-day beta vs SPY using
     ``quant_engine.universe_builder.build_high_beta`` (the library
     function — pure, no IO).

  5. Persist the beta table to ``~/.tradepro/cache/beta_scores.parquet``
     with a weekly TTL so re-runs don't re-pay the full 900-ticker
     fetch (per QUANT_ENGINE_GAPS.md Gap 2).

  6. Push the surviving tickers as a `high_beta` universe via
     ``/api/ingest/universes`` — the same ingest endpoint the
     existing Wikipedia universes use. After that the UI's universe
     picker shows it alongside sp500 / nasdaq100 / large_50 / etc.

Run modes:
    tradepro-build-high-beta-universe              # compute + push
    tradepro-build-high-beta-universe --dry-run    # compute, no push
    tradepro-build-high-beta-universe --force      # ignore cache TTL
    tradepro-build-high-beta-universe --min-beta 1.3   # override threshold
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from ..cache import ensure_cached
from ..quant_engine import BetaResult, build_high_beta
from ..quant_engine.config import QuantEngineConfig
from ..secrets import get_secret
from ..universes import wikipedia as wu

log = logging.getLogger("tradepro.cli.build_high_beta_universe")

BETA_CACHE_PATH = Path.home() / ".tradepro" / "cache" / "beta_scores.parquet"
BETA_CACHE_TTL_DAYS = 7  # weekly refresh per QUANT_ENGINE_GAPS.md Gap 2
SPY_TICKER = "SPY"


@dataclass
class BuildSummary:
    """Audit summary returned so the CLI can pretty-print + tests can assert."""
    candidates_total: int     # S&P 500 + 400 after Wikipedia scrape
    after_crypto_exclude: int
    after_data_filter: int    # had enough history to compute beta
    survivors: int            # passed beta > min_beta
    skipped_no_data: list[str]
    duration_s: float


def _resolve_creds() -> tuple[str | None, str | None]:
    base = get_secret("api-base-url") or get_secret("api-url")
    token = get_secret("ingest-api-token") or get_secret("ingest-token")
    return (base.rstrip("/") if base else None), token


def _load_cached_betas() -> pd.DataFrame | None:
    """Return the previously-computed beta table if fresh, else None.

    Weekly TTL: re-running before the window is up just reloads the
    parquet, which is what the trader's spec calls for ("recompute
    only the tickers whose cached score is older than 7 days").
    We treat the whole table as one batch — finer-grained per-ticker
    invalidation can come later if it ever matters."""
    if not BETA_CACHE_PATH.exists():
        return None
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(
        BETA_CACHE_PATH.stat().st_mtime, tz=timezone.utc,
    )
    if age > timedelta(days=BETA_CACHE_TTL_DAYS):
        log.info("beta cache is %s old (> %dd) — recomputing",
                 age, BETA_CACHE_TTL_DAYS)
        return None
    log.info("loading cached betas from %s (age %s)", BETA_CACHE_PATH, age)
    return pd.read_parquet(BETA_CACHE_PATH)


def _save_beta_table(survivors: list[BetaResult]) -> None:
    BETA_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ticker": r.ticker, "beta": r.beta, "observations": r.observations}
        for r in survivors
    ]
    pd.DataFrame(rows).to_parquet(BETA_CACHE_PATH, index=False)
    log.info("wrote %d beta rows to %s", len(rows), BETA_CACHE_PATH)


def _scrape_candidates(cfg: QuantEngineConfig) -> list[str]:
    """Scrape sp500 + sp400 from Wikipedia, normalised + de-duplicated."""
    tickers: set[str] = set()
    for name in ("sp500", "sp400_midcap"):
        try:
            symbols = wu.fetch_universe(name)
        except wu.UniverseFetchError as e:
            log.warning("%s scrape failed (%s) — skipping", name, e)
            continue
        for s in symbols:
            tickers.add(s.ticker)
    excluded = frozenset(cfg.crypto_exclude)
    pre_count = len(tickers)
    kept = sorted(tickers - excluded)
    log.info("candidates: %d (after crypto exclusion: %d → %d)",
             pre_count, pre_count, len(kept))
    return kept


def _fetch_closes(
    tickers: list[str],
    *,
    start: datetime,
    end: datetime,
    min_history: int,
) -> tuple[dict[str, pd.Series], list[str]]:
    """Return ``({ticker: close_series}, [tickers with no/short data])``.

    Uses the Parquet cache. Skips tickers whose cached series is
    shorter than ``min_history`` bars — those wouldn't survive the
    beta_lookback floor anyway and we save the user the noise. Reports
    them out so the operator can see what was dropped vs what was a
    real beta-below-threshold fail.
    """
    closes: dict[str, pd.Series] = {}
    skipped: list[str] = []
    for i, t in enumerate(tickers, 1):
        if i % 50 == 0:
            log.info("  fetched %d/%d", i, len(tickers))
        try:
            df = ensure_cached("yahoo", t, start, end, "1d")
        except Exception as e:  # noqa: BLE001 — one bad ticker shouldn't halt
            log.debug("fetch %s failed: %s", t, e)
            skipped.append(t)
            continue
        if df.empty or len(df) < min_history:
            skipped.append(t)
            continue
        # Cache stores adjusted close under "close"; standardise to a
        # Series indexed by date.
        col = "close" if "close" in df.columns else (
            "Close" if "Close" in df.columns else None
        )
        if col is None:
            skipped.append(t)
            continue
        closes[t] = df[col].copy()
    log.info("usable history: %d / %d (skipped %d)",
             len(closes), len(tickers), len(skipped))
    return closes, skipped


def _ingest_payload(survivors: list[BetaResult]) -> dict[str, object]:
    """Shape the survivor list as the API's UniverseIngestBatch."""
    return {
        "universes": [{
            "name": "high_beta",
            "source_url": "computed: β>min vs SPY (252d) — quant_engine.universe_builder",
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "quant_engine_high_beta",
            "symbols": [
                {
                    "ticker": r.ticker,
                    # Encode beta + obs count in the optional fields so
                    # downstream UI can show "β=2.3 over 252 obs" without
                    # a schema change. `sector` is the natural
                    # one-line-explainer slot since neither
                    # universe_symbols.beta nor .observations exist as
                    # columns yet.
                    "name": None,
                    "sector": f"β={r.beta:.2f}",
                    "industry": f"{r.observations} obs",
                }
                for r in survivors
            ],
        }],
    }


def _push(payload: dict, base: str, token: str, *, timeout: int = 60) -> bool:
    url = f"{base}/api/ingest/universes"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        log.error("push failed: %s", exc)
        return False
    if not (200 <= resp.status_code < 300):
        log.error("push HTTP %d: %s", resp.status_code, resp.text[:300])
        return False
    log.info("push ok: %s", resp.text[:300])
    return True


def build_and_push(
    *,
    cfg: QuantEngineConfig | None = None,
    min_beta: float | None = None,
    beta_lookback: int | None = None,
    push: bool = True,
    force: bool = False,
) -> tuple[list[BetaResult], BuildSummary]:
    """The end-to-end pipeline as one function — callable from tests
    and from main(). Returns ``(survivors, summary)``."""
    cfg = cfg or QuantEngineConfig()
    eff_min_beta = min_beta if min_beta is not None else cfg.min_beta
    eff_lookback = beta_lookback if beta_lookback is not None else cfg.beta_lookback
    started = datetime.now(timezone.utc)

    if not force:
        cached = _load_cached_betas()
        if cached is not None:
            cached = cached[cached["beta"] > eff_min_beta].copy()
            survivors = [
                BetaResult(ticker=r.ticker, beta=float(r.beta),
                           observations=int(r.observations))
                for r in cached.itertuples(index=False)
            ]
            survivors.sort(key=lambda r: r.beta, reverse=True)
            summary = BuildSummary(
                candidates_total=-1, after_crypto_exclude=-1,
                after_data_filter=len(cached), survivors=len(survivors),
                skipped_no_data=[],
                duration_s=(datetime.now(timezone.utc) - started).total_seconds(),
            )
            log.info("using cached beta table: %d survivors at β > %.2f",
                     len(survivors), eff_min_beta)
            if push and survivors:
                base, token = _resolve_creds()
                if not base or not token:
                    log.error("push requested but credentials missing")
                else:
                    _push(_ingest_payload(survivors), base, token)
            return survivors, summary

    candidates = _scrape_candidates(cfg)
    candidates_total = len(candidates) + len(cfg.crypto_exclude.intersection(candidates))
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    # eff_lookback trading days ≈ eff_lookback * (7/5) calendar days + 50d buffer
    start = end - timedelta(days=int(eff_lookback * 1.5) + 50)

    log.info("fetching SPY benchmark...")
    spy_df = ensure_cached("yahoo", SPY_TICKER, start, end, "1d")
    if spy_df.empty:
        raise RuntimeError("SPY cache fetch returned empty — cannot compute betas")
    spy_close = spy_df["close" if "close" in spy_df.columns else "Close"].copy()

    closes, skipped = _fetch_closes(
        candidates, start=start, end=end, min_history=eff_lookback + 10,
    )

    survivors = build_high_beta(
        spy_close, closes,
        min_beta=eff_min_beta,
        beta_lookback=eff_lookback,
        crypto_exclude=cfg.crypto_exclude,
    )
    log.info("survivors: %d at β > %.2f", len(survivors), eff_min_beta)

    if survivors:
        _save_beta_table(survivors)

    summary = BuildSummary(
        candidates_total=candidates_total,
        after_crypto_exclude=len(candidates),
        after_data_filter=len(closes),
        survivors=len(survivors),
        skipped_no_data=skipped,
        duration_s=(datetime.now(timezone.utc) - started).total_seconds(),
    )

    if push and survivors:
        base, token = _resolve_creds()
        if not base or not token:
            log.error(
                "push requested but credentials missing — set "
                "TRADEPRO_API_BASE_URL + TRADEPRO_INGEST_API_TOKEN "
                "or populate ~/.tradepro/credentials",
            )
        else:
            _push(_ingest_payload(survivors), base, token)

    return survivors, summary


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    p = argparse.ArgumentParser(
        prog="tradepro-build-high-beta-universe",
        description=(
            "Compute high-beta sleeve (β > min vs SPY) from S&P 500 + 400, "
            "and push as the `high_beta` universe via /api/ingest/universes."
        ),
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Compute betas but skip the API push.",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Ignore the weekly cache and recompute every beta.",
    )
    p.add_argument(
        "--min-beta", type=float, default=None,
        help="Override the QuantEngineConfig.min_beta threshold (default 1.5).",
    )
    p.add_argument(
        "--beta-lookback", type=int, default=None,
        help="Override beta_lookback in trading days (default 252).",
    )
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        survivors, summary = build_and_push(
            min_beta=args.min_beta,
            beta_lookback=args.beta_lookback,
            push=not args.dry_run,
            force=args.force,
        )
    except Exception as exc:  # noqa: BLE001 — top-level CLI handler
        log.exception("build failed: %s", exc)
        return 1

    print("\n--- summary ---")
    print(f"candidates_total      : {summary.candidates_total}")
    print(f"after_crypto_exclude  : {summary.after_crypto_exclude}")
    print(f"after_data_filter     : {summary.after_data_filter}")
    print(f"survivors             : {summary.survivors}")
    print(f"duration_s            : {summary.duration_s:.1f}")
    if survivors:
        print("\ntop 10 by beta:")
        for r in survivors[:10]:
            print(f"  {r.ticker:8s} β={r.beta:5.2f}  ({r.observations} obs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
