"""tradepro-bar-cache-harvest — batch harvest bars for the full universe.

Two modes in one command:

    # Daily cron (run from launchd at 21:15 UTC / 5:15 PM ET after close):
    tradepro-bar-cache-harvest
    # → uses IBKR primary; yfinance is acceptable fallback for today-only
    #   because TWS may briefly lag behind the cron schedule.

    # Historical backfill — IBKR-only (no yfinance stubs for old partitions):
    tradepro-bar-cache-harvest --from 2025-07-01 --to 2026-06-09 --ibkr-only
    # → TWS must be open on port 7497. If IBKR is unavailable the
    #   partition is skipped and reported as PENDING, not written with
    #   low-quality yfinance data.

    # Different resolution (daily bars = decades of history):
    tradepro-bar-cache-harvest --resolution 1d --from 2020-01-01

    # Override universe:
    tradepro-bar-cache-harvest --symbols "SPY,QQQ,NVDA"

IBKR history limits per resolution:
    1m  →  ~1 year back,  30-day request chunks
    5m  →  ~3 years back, 60-day request chunks
    1d  →  decades,       365-day request chunks

Data quality tiers (shown in scorecard):
    GOLD   — IBKR source, ≥90 % sessions covered
    SILVER — IBKR source, <90 % sessions covered (gaps / partial month)
    BRONZE — yfinance or IG source (acceptable for today-only; not for backtest)
    MISSING — no cached data at all

Provider precedence (always):
    ibkr → ig → yfinance
    --ibkr-only removes ig and yfinance from the chain so gaps stay explicit.

Exit codes:
    0  all symbols fully covered
    1  partial — some symbols have gaps (weekend/holiday gaps expected;
       IBKR market data farm outages produce this)
    2  fatal — every symbol failed
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tradepro_strategies.bar_cache import BarFetchError, BarStore, PreferencesLoader
from tradepro_strategies.bar_cache.asset_classes import UsEtfPlugin  # noqa: F401 — registers
from tradepro_strategies.bar_cache.quality import fetch_tier, fetch_tier_icon
from tradepro_strategies.bar_cache.providers import YFinanceProvider  # noqa: F401 — registers
from tradepro_strategies.bar_cache.providers.ibkr_provider import (
    IBKRProvider,  # noqa: F401 — registers
)
from tradepro_strategies.bar_cache.telemetry import BackendTelemetrySink, TelemetrySink

# Full universe: intraday_flat candidates + SPY (regime filter).
# Keep in sync with intraday_flat.default_params()["candidates"].
_DEFAULT_SYMBOLS = [
    "SPY",                                            # regime filter
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
    "META", "TSLA", "QQQ",  "AMD",  "NFLX", "AVGO",
]

_DEFAULT_BASE_DIR = Path.home() / ".tradepro" / "bar_cache"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="tradepro-bar-cache-harvest",
        description=(
            "Batch-harvest bars for the intraday_flat universe via IBKR TWS.\n\n"
            "Daily mode (default, no --from): harvest today's session.\n"
            "Backfill mode (explicit --from): harvest historical data up to\n"
            "IBKR's limit (~1 year of 1m bars, decades of daily bars)."
        ),
    )
    parser.add_argument(
        "--symbols", default=None,
        help=(
            "Comma-separated symbol list "
            "(default: SPY + full intraday_flat universe)"
        ),
    )
    parser.add_argument(
        "--universe", default=None,
        help=(
            "Comma-separated universe NAME(s) (e.g. 'large_50,high_beta') — "
            "harvest the SAME effective tickers the strategy desks trade, loaded "
            "live from /api/universes/<name>. This is how you harvest the FULL "
            "backtest universe instead of the hardcoded 12-symbol default. "
            "Takes precedence over --symbols. Requires the API to be reachable."
        ),
    )
    parser.add_argument(
        "--asset", default="us_etf",
        help="Asset class plugin (default: us_etf)",
    )
    parser.add_argument(
        "--resolution", default="1m",
        choices=["1m", "5m", "15m", "30m", "1h", "1d"],
        help="Bar resolution (default: 1m)",
    )
    parser.add_argument(
        "--from", dest="from_date", default=None,
        help=(
            "Start date YYYY-MM-DD. Omit for daily-cron mode (today only). "
            "Set to e.g. 2025-07-01 for a historical backfill."
        ),
    )
    parser.add_argument(
        "--to", dest="to_date", default=None,
        help="End date YYYY-MM-DD inclusive (default: today)",
    )
    parser.add_argument(
        "--base-dir", default=str(_DEFAULT_BASE_DIR),
        help=f"Cache root directory (default: {_DEFAULT_BASE_DIR})",
    )
    parser.add_argument(
        "--allow-partial", action="store_true",
        help=(
            "Don't exit 1 on gaps — expected for weekend/holiday dates. "
            "Still prints which sessions were missing."
        ),
    )
    parser.add_argument(
        "--force-refresh", action="store_true",
        help=(
            "RE-FETCH partitions that are already cached and OVERWRITE them. "
            "Without this a cached partition always wins, so bars written by a "
            "fallback provider are never re-sourced even once the golden source "
            "is working again — which is why the intraday store stayed "
            "yfinance-heavy after ibkr_web recovered. Pair with --ibkr-only so "
            "a fallback cannot write into the window you are cleaning. Safe by "
            "construction: the store refuses to replace a partition with FEWER "
            "rows than it already holds (partial_write_refused) and never "
            "overwrites good data with an empty response."
        ),
    )
    parser.add_argument(
        "--api-base", default=None,
        help=(
            "Optional API base URL (e.g. http://16.60.201.137). "
            "When set, telemetry events are POST-ed to EC2 so the cockpit "
            "Data Health panel reflects this harvest run."
        ),
    )
    parser.add_argument(
        "--auth-token", default=None,
        help="Bearer token for --api-base. Falls back to TRADEPRO_API_TOKEN env var.",
    )
    parser.add_argument(
        "--ibkr-only", action="store_true",
        help=(
            "Use IBKR as the ONLY provider — do not fall back to IG or yfinance. "
            "Required for historical backfill to prevent low-quality yfinance stubs "
            "(7-day 1m limit) being written into old partitions. "
            "Partitions that IBKR cannot serve are reported as PENDING rather than "
            "written with fallback data. TWS must be open on port 7497."
        ),
    )
    parser.add_argument(
        "--no-ibkr", action="store_true",
        help=(
            "Skip IBKR (and IG) — use yfinance ONLY. For the scheduled DAILY "
            "harvest: IBKR historical hangs under gateway contention (the trader "
            "daemon holds the single session), and yfinance daily bars are fast + "
            "adequate. Also disables the API provider-preference so it can't "
            "re-add IBKR; telemetry still pushes."),
    )
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="INFO-level logging + per-partition paths")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # ── Symbols ────────────────────────────────────────────────
    # Precedence: --universe (the FULL strategy universe, live from the API) >
    # --symbols (explicit list) > the hardcoded 12-symbol intraday default. The
    # default exists only so a no-arg run still does something; credible
    # full-universe backtests need --universe.
    if args.universe:
        from tradepro_strategies.cli.paper_session import _fetch_universe_symbols
        symbols = []
        seen: set[str] = set()
        for uname in [u.strip() for u in args.universe.split(",") if u.strip()]:
            for s in _fetch_universe_symbols(uname):  # fail-loud on empty
                if s not in seen:
                    seen.add(s)
                    symbols.append(s)
        logging.getLogger("tradepro.harvest").info(
            "harvest universe %s → %d symbols", args.universe, len(symbols))
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = list(_DEFAULT_SYMBOLS)

    # ── Dates ─────────────────────────────────────────────────
    today_utc = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    from_date = _parse_date(args.from_date) if args.from_date else today_utc
    # --to is inclusive; store uses half-open [start, end) so +1 day
    to_date = (
        (_parse_date(args.to_date) + timedelta(days=1))
        if args.to_date
        else (today_utc + timedelta(days=1))
    )

    # ── Store ──────────────────────────────────────────────────
    base_dir = Path(args.base_dir).expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)

    if args.api_base:
        token = args.auth_token or os.environ.get("TRADEPRO_API_TOKEN")
        telemetry = BackendTelemetrySink(
            base_dir=base_dir, api_base=args.api_base, auth_token=token,
        )
        # --no-ibkr forces the local yfinance chain; skip the preference loader
        # so an API-side ibkr-first preference can't re-add IBKR (and re-hang).
        preferences_loader = None if args.no_ibkr else PreferencesLoader(
            api_base=args.api_base, auth_token=token,
        )
    else:
        telemetry = TelemetrySink(base_dir=base_dir)
        preferences_loader = None

    # Provider chain depends on mode:
    # --ibkr-only (historical backfill): IBKR only — gaps stay explicit,
    #   no yfinance stubs for old partitions.
    # daily mode (no --ibkr-only): full chain — yfinance is an acceptable
    #   same-day fallback if TWS briefly lags.
    # ibkr_web (OAuth Web API via the central backend endpoint) is the WORKING
    # IBKR-GOOD source — preferred FIRST. The legacy 'ibkr' (local
    # Gateway/ib_insync) stays only as a fallback; it hangs on session
    # contention and was RETIRED by owner ruling on 9 Aug 2026 (OAuth-only,
    # code must be runnable off-Mac).
    #
    # CORRECTION 16 Aug 2026: this comment used to claim "only [the Gateway]
    # does 1m". That is FALSE and it cost an investigation — ibkr_web's _BAR map
    # covers 1d/1h/1m/5m/15m/30m, and it was verified serving 649 one-minute and
    # 129 five-minute bars for SPY on demand. Because of the stale claim, a
    # rate-limited ibkr_web failure surfaced as "PENDING — IBKR unavailable (TWS
    # closed?)" and pointed at a Gateway that no longer exists. We do NOT need
    # the Gateway for intraday. See migration 055 / Data Accuracy Turnaround.
    # THE LOCAL GATEWAY IS RETIRED (owner ruling 9 Aug 2026: OAuth-only, the
    # code must run off-Mac). Leaving `ibkr` in the chain costs a
    # ConnectionRefused on 127.0.0.1:7500 for EVERY SYMBOL — which is what
    # pushed the 5-minute harvest past its 60-minute deadline on every run
    # (starts 91 minutes apart: 60 of deadline + the 30-minute interval), so it
    # never reached "Done:" and the lane reported 'fail' for 19 hours.
    # Re-enable only if you actually have TWS open locally.
    _gw = os.environ.get("TRADEPRO_USE_LOCAL_GATEWAY", "0").strip().lower() in ("1", "true", "yes", "on")
    if args.ibkr_only:
        chain = ["ibkr_web"] + (["ibkr"] if _gw else [])
        # --ibkr-only ALREADY says "do not fall back". So WAIT OUT auth
        # cooldowns rather than fast-failing into cache — the default budget of
        # 3 waits is sized for the nightly harvest (one request per symbol) and
        # drains instantly on a backfill making hundreds of chunked requests.
        # That is what returned "SPY 4978/58518 bars ... 0 failed" on 29 Aug:
        # 8% of the data, reported green, while IBKR was perfectly healthy.
        #
        # Owner, twice, and it is a standing rule rather than a preference:
        # "we just have to retry with force" and "we do not need super fast
        # response". Correctness over speed when the golden source is the
        # explicit ask. An explicit env var still wins, so this raises the
        # floor without taking the choice away.
        _want = os.environ.get("TRADEPRO_IBKR_COOLDOWN_WAITS")
        if _want is None:
            os.environ["TRADEPRO_IBKR_COOLDOWN_WAITS"] = "40"
            print("  --ibkr-only: waiting out auth cooldowns (budget 40) rather "
                  "than serving cache — set TRADEPRO_IBKR_COOLDOWN_WAITS to override")
    elif args.no_ibkr:
        chain = ["yfinance"]
    else:
        chain = ["ibkr_web"] + (["ibkr"] if _gw else []) + ["ig", "yfinance"]

    store = BarStore(
        base_dir=base_dir,
        telemetry=telemetry,
        preferences_loader=preferences_loader,
        provider_chain=chain,
    )

    # ── Header ─────────────────────────────────────────────────
    span_days = (to_date - from_date).days
    mode_label = (
        "daily" if span_days <= 1
        else f"backfill {span_days}d ({from_date.date()} → {(to_date - timedelta(days=1)).date()})"
    )
    chain_label = ("ibkr_web→ibkr" if args.ibkr_only
                   else "yfinance-only" if args.no_ibkr else "ibkr_web→ibkr→ig→yfinance")
    print(
        f"tradepro-bar-cache-harvest  mode={mode_label}  "
        f"res={args.resolution}  symbols={len(symbols)}  "
        f"chain={chain_label}"
    )
    if args.ibkr_only:
        print("  ⚡ IBKR-only mode: gaps will be reported as PENDING — no yfinance fallback")
    if args.force_refresh:
        print("  ♻  FORCE-REFRESH: cached partitions will be RE-FETCHED AND OVERWRITTEN")
        if not args.ibkr_only:
            print("     ⚠ WITHOUT --ibkr-only a fallback provider may write into the "
                  "window you are trying to clean")
    print("-" * 70)

    # ── Harvest loop ───────────────────────────────────────────
    ok_count = partial_count = fail_count = 0
    # Track quality tier counts: gold=ibkr complete, silver=ibkr partial,
    # bronze=yfinance/ig, missing=all failed
    quality_counts: dict[str, int] = {"gold": 0, "silver": 0, "bronze": 0, "missing": 0}
    # Who actually served, and why the golden source didn't (27 Aug 2026).
    # A run can report "0 failed" while serving 97% of its symbols from the
    # fallback — that is what happened on 27 Aug (237/244 yfinance) and nothing
    # in the output said so.
    from collections import Counter as _Counter
    _source_counts: _Counter = _Counter()
    _cache_served = 0
    # Symbols where --force-refresh was asked for and CACHE came back instead.
    _refresh_denied: list[str] = []
    _demotion_counts: _Counter = _Counter()
    # Per-symbol health records → POSTed to the cockpit's data-trust DB after the
    # run so the Harvest/Data-Health screen renders the real coverage (bridges
    # the local-cache → EC2 gap). Best-effort; never blocks the harvest.
    health_records: list[dict] = []

    # Pace IBKR requests. On 16 Aug a batch re-source fired 11 symbols inside
    # ONE SECOND (17:07:07.275 → .954); SPY succeeded and every symbol after it
    # failed, because the backend drops into auth cooldown under that load. The
    # data was fine — AAPL fetched normally moments later — so this was pure
    # self-inflicted throttling. A background harvest has no deadline, so
    # spending a second per symbol to get the GOLDEN source instead of a
    # fallback is the right trade (the same reasoning as 9255cc5).
    _ibkr_in_chain = any(p.startswith("ibkr") for p in chain)
    _pace_s = float(os.environ.get("TRADEPRO_HARVEST_PACE_S", "1.0")) if _ibkr_in_chain else 0.0

    # CIRCUIT BREAKER (22 Aug 2026). On a dead-provider day (IBKR session
    # dark + Yahoo rate-limited) every symbol still burned a doomed fetch
    # attempt plus retries before its cache served — a 224-symbol sweep took
    # 48 minutes to deliver what the disk already held. After N consecutive
    # whole-chain failures, stop asking: serve cache-only for the rest of the
    # run, probing one real fetch every P symbols so a mid-run recovery
    # closes the circuit. The next scheduled run always starts closed.
    _breaker_n = int(os.environ.get("TRADEPRO_HARVEST_BREAKER_N", "5"))
    _probe_every = max(1, int(os.environ.get("TRADEPRO_HARVEST_BREAKER_PROBE", "20")))
    _consec_fail = 0
    _circuit_open = False
    _skipped_fetches = 0

    for idx, symbol in enumerate(symbols):
        _probe = _circuit_open and (idx % _probe_every == 0)
        _skip = _circuit_open and not _probe
        if _pace_s and idx and not _skip:
            time.sleep(_pace_s)
        try:
            # RETRY TRANSIENTS (16 Aug 2026). Without this a batch always
            # leaves stragglers: in one 7-symbol run six converted to IBKR and
            # META failed alone, then fetched fine moments later. A single
            # transient must not be the difference between a golden bar and a
            # yfinance one written permanently — a cache hit is never
            # re-sourced, so today's blip becomes next year's provenance.
            # Bounded and paced; a genuinely dead symbol still fails loud.
            _attempts = int(os.environ.get("TRADEPRO_HARVEST_RETRIES", "3")) if _ibkr_in_chain else 1
            if _skip:
                _attempts = 1       # cache read can't fail transiently
                _skipped_fetches += 1
            result = None
            _last: Exception | None = None
            for _try in range(max(1, _attempts)):
                try:
                    result = store.get(
                        canonical=symbol,
                        asset_class=args.asset,
                        resolution=args.resolution,
                        start=from_date,
                        end=to_date,
                        allow_partial=True,   # always read what's there; we report below
                        force_refresh=args.force_refresh,
                        fetched_by=os.environ.get("USER", "harvest"),
                        skip_fetch=_skip,
                    )
                    break
                except BarFetchError as _exc:
                    _last = _exc
                    # A schema/unsupported-resolution failure will never heal;
                    # only wait on the ones that plausibly will.
                    if _exc.error_class in ("schema", "manifest"):
                        raise
                    if _try + 1 >= max(1, _attempts):
                        raise
                    _backoff = 5 * (2 ** _try)   # 5s, 10s
                    print(f"    ↻ {symbol:<8s} {_exc.error_class} — retry "
                          f"{_try + 1}/{_attempts - 1} in {_backoff}s", flush=True)
                    time.sleep(_backoff)
            assert result is not None  # loop either breaks with a result or raises
            # Breaker accounting: a real provider answer closes the circuit;
            # a fetch that failed into cache-serving opens it further. Pure
            # cache hits say nothing about the chain and leave it unchanged.
            _tried = getattr(result, "provider_chain_tried", None) or []
            if any(str(e).endswith("_ok") for e in _tried):
                _consec_fail = 0
                if _circuit_open:
                    _circuit_open = False
                    print(f"  ⚡ circuit CLOSED at {symbol} — providers answering again")
            elif "fetch_failed_serving_cache" in _tried:
                _consec_fail += 1
            tier = _quality_tier(result.provider_used, result.coverage_complete,
                                 getattr(result, "df", None))
            tier_icon = _tier_icon(tier)
            quality_counts[tier] += 1

            if result.coverage_complete:
                ok_count += 1
                mark = "✓"
            else:
                partial_count += 1
                mark = "~"

            # WHY the golden source was not used (27 Aug 2026). This line used
            # to print `source=yfinance_ok` and stop, which is how a run served
            # 237 of 244 symbols from Yahoo with no recorded reason. IBKR is the
            # golden source and Yahoo a VISIBLE fallback — "visible" has to mean
            # the demotion states its cause, or a silent all-Yahoo night looks
            # identical to a healthy one.
            #
            # The reason was never lost, only discarded: `provider_chain_tried`
            # already holds entries like `ibkr_web_rate_limited` /
            # `ibkr_web_out_of_range` from the chain walk. Print them whenever
            # something other than the chain's FIRST provider ends up serving.
            # A CACHE hit is not a demotion. Serving cache means the store had
            # nothing new to fetch, which is the CORRECT outcome — most of all
            # after the delta-unsettled clamp, where declining to ask for a bar
            # that cannot exist yet is the whole point. Counting it as "the
            # golden source was degraded" would fire this warning on every
            # healthy run, and a warning that fires when nothing is wrong is
            # how the previous quality signal in this file had to be walked
            # back (see the `_tier` note below). Only a real FALLBACK PROVIDER
            # writing bars is a demotion.
            _used = str(result.provider_used or "unknown")
            _from_cache = _used.startswith(("cache", "bar_cache"))
            _demoted = ""
            _chain = list(getattr(result, "provider_chain_tried", None) or [])
            _primary = (chain or [None])[0]
            if (_primary and not _from_cache
                    and not _used.startswith(str(_primary))):
                _why = [c for c in _chain if str(c).startswith(f"{_primary}_")]
                if _why:
                    _demoted = f"  ← {_primary} declined: {', '.join(_why[:3])}"
                    _demotion_counts[str(_why[0])] += 1
                else:
                    _demoted = f"  ← {_primary} did not serve (no reason recorded)"
                    _demotion_counts[f"{_primary}_no_reason"] += 1
            _source_counts[_used] += 1
            if _from_cache:
                _cache_served += 1

            # A REFUSED REFRESH IS NOT A SUCCESS (31 Aug 2026).
            #
            # Serving cache on an ORDINARY run is correct and must stay quiet —
            # that is the note above, and the cry-wolf this file already walked
            # back once. But --force-refresh is an explicit instruction to
            # re-source, so cache coming back means the instruction was NOT
            # carried out, and the run printed:
            #
            #     ✓ TSLA   9/9 bars   🥇 gold   source=cache
            #
            # while IBKR was unreachable. Tick, GOLD, exit 0. The grade was even
            # accurate — it grades what is ON DISK — but the RUN did nothing,
            # and nothing said so. That is how "--ibkr-only --force-refresh
            # doesn't work" got diagnosed as an inert flag when the real story
            # was IBKR flapping every ~15 minutes.
            #
            # --ibkr-only promises "gaps will be reported as PENDING — no
            # yfinance fallback". Reporting a cache hit as a tick breaks that
            # promise in the one mode that exists to keep it.
            if args.force_refresh and _from_cache:
                _refresh_denied.append(symbol)
                mark = "!"
                _demoted += ("  ← FORCE-REFRESH NOT HONOURED: served cache, bars unchanged"
                             + (" (--ibkr-only forbids a fallback, so IBKR did not answer)"
                                if args.ibkr_only else ""))

            print(
                f"  {mark} {symbol:<8s} "
                f"{result.rows_returned:6d}/{result.rows_expected:<6d} bars  "
                f"{tier_icon} {tier:<8s}  "
                f"source={result.provider_used}{_demoted}"
            )
            # Report the TRUE history depth (earliest bar across ALL partitions),
            # NOT just this fetch's trailing window — otherwise the Data Health
            # screen shows ~10 days when the cache actually holds years (66mo for
            # large_50). The nightly 10-day harvest was overwriting the full
            # coverage the backfill reported. Cheap: index-only parquet reads.
            import glob as _glob
            import pandas as _pd
            _pq = _glob.glob(str(base_dir / args.asset / symbol / args.resolution / "*.parquet"))
            _cov_start = None
            _rows_on_disk = 0
            for _f in _pq:
                try:
                    _idx = _pd.to_datetime(_pd.read_parquet(_f).index)
                    _rows_on_disk += len(_idx)
                    _mn = _idx.min().date().isoformat()
                    if _cov_start is None or _mn < _cov_start:
                        _cov_start = _mn
                except Exception:  # noqa: BLE001
                    continue
            # "Missing days" judged against the SYMBOL'S OWN coverage span,
            # never against this run's requested window. 22 Aug 2026: a
            # 13-year re-source probe (--from 2013) made 5 symbols report
            # "2,288-3,227 missing days" on the Data screen when their disk
            # was complete for the span they actually hold — the metric was
            # inheriting whatever window the last run happened to ask for.
            _missing = max(0, int(result.rows_expected) - int(result.rows_returned))
            if _cov_start is not None:
                try:
                    from tradepro_strategies.bar_cache.asset_class import (
                        get_asset_class as _gac,
                    )
                    _plugin = _gac(args.asset)
                    _span_start = datetime.strptime(_cov_start, "%Y-%m-%d").replace(tzinfo=UTC)
                    _expected_span = sum(
                        _plugin.expected_bar_count(args.resolution, _d)
                        for _d in _plugin.expected_session_dates(_span_start, to_date)
                        if datetime(_d.year, _d.month, _d.day, tzinfo=UTC) < to_date
                    )
                    _missing = max(0, _expected_span - _rows_on_disk)
                except Exception:  # noqa: BLE001 — fall back to window math
                    pass
            # Report WHERE THE DATA CAME FROM, not which code path answered
            # this call. Since delta-fetching (21 Aug) most runs are cache
            # serves, and posting provider="cache" made the Data screen grade
            # every symbol BRONZE while the disk was overwhelmingly IBKR-gold
            # — the same call-path/provenance conflation as the 16 Aug "0
            # gold" incident, one level further downstream. Grade from the
            # stored source column, exactly like the G/S/B summary does.
            _prov_true = (result.provider_used or "").removesuffix("_ok")
            if _prov_true == "cache" and result.df is not None \
                    and not result.df.empty and "source" in result.df.columns:
                _srcs = result.df["source"].dropna()
                if len(_srcs):
                    _prov_true = str(_srcs.mode().iloc[0])
            health_records.append({
                "canonical": symbol,
                "assetClass": args.asset,
                "lastFetchedResult": "ok" if result.coverage_complete else "partial",
                "lastFetchedProvider": _prov_true,
                "lastFetchedResolution": args.resolution,
                "coverageStartDate": _cov_start or str(from_date)[:10],  # TRUE earliest bar
                "coverageEndDate": str(to_date)[:10],
                "coveragePartitions": len(_pq),                          # real partition count
                "missingDaysCount": _missing,
            })
        except BarFetchError as exc:
            fail_count += 1
            _consec_fail += 1
            # "MISSING" MUST MEAN "NO DATA", NOT "THIS FETCH FAILED".
            #
            # 16 Aug 2026, the THIRD instance of this exact conflation (after
            # cache→bronze, and provider_used="cache" in bars_provenance): a
            # maintenance re-source hit transient IBKR errors on 104 symbols and
            # the Data screen announced "104 of 251 symbols have NO data" — while
            # every one of those symbols had a full cached history on disk. A
            # failed fetch says something about the RUN; it says nothing about
            # the data. Grade on what is actually on disk.
            _cached = None
            try:
                _cached = store.get(
                    canonical=symbol, asset_class=args.asset,
                    resolution=args.resolution, start=from_date, end=to_date,
                    allow_partial=True, fetched_by="harvest-fallback-read",
                )
            except Exception:  # noqa: BLE001 — genuinely nothing there
                _cached = None
            if _cached is not None and _cached.df is not None and not _cached.df.empty:
                _t = _quality_tier("cache", _cached.coverage_complete, _cached.df)
                quality_counts[_t] += 1
                print(f"  ! {symbol:<8s} fetch failed ({exc.error_class}) — "
                      f"KEEPING {_cached.rows_returned} cached bars [{_t}]")
                continue
            quality_counts["missing"] += 1
            # Be explicit: IBKR-only mode means "PENDING — open TWS to fill".
            #
            # 16 Aug 2026: this message actively MISDIRECTED. A batch re-source
            # showed SPY succeeding then 11 symbols "PENDING — IBKR unavailable
            # (TWS closed?)", which sent me chasing a local Gateway that was
            # RETIRED weeks ago (owner ruling 9 Aug: OAuth-only). The truth was
            # that `ibkr_web` — the provider that matters — had failed silently
            # from rate-limiting, and the only visible error came from the dead
            # `ibkr` fallback further down the chain. AAPL fetched fine from
            # ibkr_web moments later.
            #
            # So name what the GOLDEN provider actually said. A failure message
            # that points at the wrong subsystem is worse than none: it costs an
            # investigation and teaches the wrong lesson.
            if args.ibkr_only and "no_provider" in exc.error_class:
                _tried = ", ".join(getattr(exc, "attempted", None) or []) or "unknown"
                _cause = getattr(exc, "__cause__", None)
                _why = f"{type(_cause).__name__}: {str(_cause)[:90]}" if _cause else "no detail"
                print(f"  ⏳ {symbol:<8s} PENDING  — chain [{_tried}] exhausted; "
                      f"last error {_why}")
            else:
                print(
                    f"  ✗ {symbol:<8s} "
                    f"MISSING  — {exc.error_class}: {str(exc)[:60]}"
                )
        except Exception as exc:  # noqa: BLE001
            fail_count += 1
            quality_counts["missing"] += 1
            print(f"  ✗ {symbol:<8s} ERROR: {exc}")

        if not _circuit_open and _consec_fail >= _breaker_n:
            _circuit_open = True
            print(f"  ⚡ circuit OPEN after {_consec_fail} consecutive provider "
                  f"failures — serving cache-only, probing every "
                  f"{_probe_every} symbols")

    # ── Summary ────────────────────────────────────────────────
    print("-" * 70)
    # "complete" here means the COVERAGE on disk is complete, which is a real and
    # useful thing to say. But printing "244 complete / 0 partial / 0 failed"
    # directly above "FORCE-REFRESH DID NOTHING for 244 of 244" reads as a
    # contradiction, and the reader has to work out which line to believe. Say
    # both facts on the same line so they cannot be read apart.
    _not_refreshed = (f"  ({len(_refresh_denied)} NOT refreshed)"
                      if _refresh_denied else "")
    print(
        f"Done: {ok_count} complete  "
        f"{partial_count} partial  "
        f"{fail_count} failed  "
        f"/ {len(symbols)} symbols{_not_refreshed}"
    )
    print(
        f"Quality: "
        f"🥇 {quality_counts['gold']} GOLD  "
        f"🥈 {quality_counts['silver']} SILVER  "
        f"🥉 {quality_counts['bronze']} BRONZE  "
        f"✗ {quality_counts['missing']} MISSING"
    )
    # WHO SERVED. Quality tiers describe coverage, not provenance, so a run
    # entirely on the fallback still prints a respectable-looking tier mix —
    # 27 Aug read "0 failed / 237 BRONZE" while IBKR had served 4 of 244 and
    # said nothing about why. IBKR is the golden source; a night where it
    # served almost nothing is an incident, and must read as one.
    if _source_counts:
        _total = sum(_source_counts.values())
        _parts = ", ".join(f"{k} {v}" for k, v in _source_counts.most_common())
        print(f"Sources: {_parts}")
        _primary = (chain or [None])[0]
        _prim_n = sum(v for k, v in _source_counts.items()
                      if _primary and k.startswith(str(_primary)))
        # Judge the share against symbols that actually needed a FETCH. Cache
        # hits did not exercise the chain, so counting them as a golden-source
        # miss would flag every healthy incremental run.
        _fetched = _total - _cache_served
        _share = (_prim_n / _fetched * 100.0) if _fetched else 100.0
        if _primary and _fetched and _share < 50.0:
            print(f"  ⚠ GOLDEN SOURCE DEGRADED — {_primary} served {_prim_n}/{_fetched} "
                  f"({_share:.0f}%) of the symbols that needed fetching; "
                  f"the rest came from FALLBACK providers.")
            if _demotion_counts:
                for _reason, _n in _demotion_counts.most_common(4):
                    print(f"      {_n:>4} × {_reason}")
            else:
                print("      no reason recorded by the chain walk — investigate "
                      "the provider directly; it may be failing before it reports.")
    if _skipped_fetches:
        print(f"  ⚡ circuit breaker skipped {_skipped_fetches} doomed fetch(es) "
              f"— cached data served instead; next run retries normally")
    if _refresh_denied:
        # State the number, name the symbols, and say what to do — a
        # force-refresh that quietly changed nothing is worse than one that
        # fails, because the operator walks away believing the data was cleaned.
        _shown = ", ".join(_refresh_denied[:12])
        _more = f" (+{len(_refresh_denied) - 12} more)" if len(_refresh_denied) > 12 else ""
        print(f"\n  ‼ FORCE-REFRESH DID NOTHING for {len(_refresh_denied)} of "
              f"{len(symbols)} symbol(s): {_shown}{_more}")
        print(f"      Cache was served, so their bars are UNCHANGED — this run did "
              f"not re-source anything for them.")
        print(f"      Usual cause: the provider was unreachable at that moment. "
              f"IBKR market data flaps; check /api/run-log for `ibkr-health` "
              f"quote=False around this run, then re-run.")
    if fail_count and args.ibkr_only:
        print(
            f"\n  ⏳ {fail_count} symbol(s) PENDING — open TWS on port 7497 and re-run:\n"
            f"     TRADEPRO_IBKR_PORT=7497 tradepro-bar-cache-harvest "
            f"--from {(from_date).date()} --to {(to_date - timedelta(days=1)).date()} "
            f"--ibkr-only --verbose"
        )
    elif fail_count:
        print(
            f"\n  ⚠  {fail_count} symbol(s) missing — run with --ibkr-only when TWS is open "
            f"to replace any BRONZE yfinance stubs with GOLD IBKR data."
        )

    if not args.allow_partial and partial_count:
        print(
            "  (partial gaps expected on non-trading days; "
            "use --allow-partial to suppress exit-1)"
        )

    # Report per-symbol health to the cockpit data-trust DB so the Harvest /
    # Data-Health screen renders the real coverage. Best-effort: any failure is
    # logged and ignored — it must never fail the harvest.
    if health_records:
        try:
            import requests as _rq

            from tradepro_strategies.cli import push_to_api as _pta
            _base, _tok = _pta.load_credentials()
            _h = {"Authorization": f"Bearer {_tok}"}
            _n = 0
            _dropped: list[str] = []
            for _rec in health_records:
                # Retry ONCE on any failure — 22 Aug 2026: fire-and-forget
                # dropped 3 of 5 records silently and the Data screen simply
                # never showed those symbols. A record that fails twice is
                # NAMED, not vanished.
                _ok = False
                for _attempt in (1, 2):
                    try:
                        _r = _rq.post(f"{_base.rstrip('/')}/api/admin/data-trust/bar-cache/health",
                                      headers=_h, json=_rec, timeout=15)
                        if _r.status_code in (200, 201):
                            _ok = True
                            break
                    except Exception:  # noqa: BLE001 — retry then report
                        pass
                    if _attempt == 1:
                        time.sleep(1.0)
                if _ok:
                    _n += 1
                else:
                    _dropped.append(str(_rec.get("canonical")))
            print(f"  ↑ reported health for {_n}/{len(health_records)} symbol(s) to the cockpit")
            if _dropped:
                print(f"  ⚠ health records DROPPED after retry for: {', '.join(_dropped)} "
                      f"— these symbols will look stale on the Data screen")
        except Exception as _exc:  # noqa: BLE001
            print(f"  (health report skipped: {_exc})")

    # Central run-log: record this harvest run (+ any non-GOOD outcome) so a failed or
    # partial harvest is LOUD in the cross-machine cockpit, not just in this stdout.
    try:
        from tradepro_strategies.run_log import log_run
        # A run that was ASKED to re-source and served cache instead did not do
        # its job, so it may not log "ok" — the cockpit is where a silent
        # no-op has to become visible.
        _rl_status = ("fail" if fail_count else
                      "partial" if (partial_count or _refresh_denied) else "ok")
        _rl_error = (f"{fail_count} symbol(s) missing" if fail_count else
                     (f"force-refresh not honoured for {len(_refresh_denied)} "
                      f"symbol(s) — cache served, bars unchanged"
                      if _refresh_denied else None))
        log_run(
            "bar-cache-harvest", "harvest", _rl_status,
            error=_rl_error,
            summary=(f"{args.resolution} {len(symbols)} sym → "
                     f"{quality_counts['gold']}G/{quality_counts['silver']}S/"
                     f"{quality_counts['bronze']}B/{quality_counts['missing']}M"),
        )
    except Exception:  # noqa: BLE001 — logging must never fail the harvest
        pass

    if fail_count == len(symbols):
        return 2
    if (partial_count or fail_count) and not args.allow_partial:
        return 1
    return 0


# Grading vocabulary is owned by bar_cache.quality — BOTH schemes live there
# so a future divergence shows up in one diff. `_quality_tier` and `_tier_icon`
# stay as names here because tests and readers already know them; the logic is
# no longer duplicated.
_quality_tier = fetch_tier
_tier_icon = fetch_tier_icon


def _parse_date(s: str) -> datetime:
    """YYYY-MM-DD → tz-aware UTC midnight."""
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)


if __name__ == "__main__":
    sys.exit(main())
