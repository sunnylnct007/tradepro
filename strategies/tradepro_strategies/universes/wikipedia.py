"""Wikipedia-driven symbol-universe scraper.

The trader's manual workflow today is to open the Wikipedia
"List of S&P 500 companies" / "FTSE 100" / etc. constituent pages,
copy the ticker column into a spreadsheet, normalise the dots
(BRK.B → BRK-B for Yahoo), and paste the result into the Trigger
form on the cockpit. We're automating that, end-to-end:

  1. ``fetch_universe(name)`` parses the page with ``pandas.read_html``
     (which is in the dep set already), pulls the right column out of
     the constituents table, and runs the per-universe normaliser so
     Yahoo-shaped tickers come out the other side.
  2. ``fetch_all_universes()`` iterates the registry and isolates
     per-universe errors — one broken page does not nuke the batch.
  3. ``cli/refresh_universes.py`` wraps this in a console-script
     (``tradepro-refresh-universes``) so launchd / cron can drive
     the scrape on a daily cadence.

Tests live in ``strategies/features/wiki_universes.feature`` + the
matching steps file. They use synthetic HTML fixtures — no test
ever hits Wikipedia.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable

import requests


log = logging.getLogger("tradepro.universes.wikipedia")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class UniverseFetchError(RuntimeError):
    """Raised when a single universe can't be fetched / parsed.

    Wrapped (not propagated) by ``fetch_all_universes`` so one broken
    universe doesn't kill the batch. CLI callers should re-raise to
    fail loudly when running a single-universe fetch interactively.
    """


@dataclass(frozen=True)
class Symbol:
    """One row from a constituent list.

    ``ticker`` is the *normalised* (Yahoo-friendly) ticker; the raw
    string from the Wikipedia table is dropped. Other fields are
    best-effort — some universes don't surface ``industry`` cleanly
    so it stays an empty string rather than None to keep downstream
    serialisation predictable.
    """

    ticker: str
    name: str
    sector: str
    industry: str
    source_url: str
    fetched_at_utc: str

    def to_dict(self) -> dict[str, str]:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "sector": self.sector,
            "industry": self.industry,
            "source_url": self.source_url,
            "fetched_at_utc": self.fetched_at_utc,
        }


@dataclass(frozen=True)
class UniverseDef:
    """Static metadata that drives a single scrape.

    ``table_index`` is the position of the constituents table on the
    Wikipedia page as ``pandas.read_html`` enumerates it (usually 0
    for the simpler pages, 4 for NASDAQ-100 where there's a lot of
    summary tables ahead of the constituents).

    ``symbol_column`` / ``name_column`` / ``sector_column`` are the
    *labels* (case-insensitive substring match) for the columns we
    want. We don't use positional indexes because the column order
    drifts whenever Wikipedia editors reformat the page.

    ``normaliser`` is called with the raw ticker string and returns
    the Yahoo-style ticker. Defaults to a no-op for US universes
    where the source already matches Yahoo.
    """

    url: str
    table_index: int
    symbol_column: str
    name_column: str = "company"
    sector_column: str = "sector"
    industry_column: str = "industry"
    normaliser: Callable[[str], str] = field(default=lambda s: s)
    # Some pages list the constituents under multiple consecutive
    # tables (e.g. S&P 400 / 600 split alphabetically into pages).
    # Empty = just the single table_index.
    extra_tables: tuple[int, ...] = ()


# ---------------------------------------------------------------------------
# Ticker normalisers
# ---------------------------------------------------------------------------


def _normalise_us_dot(ticker: str) -> str:
    """US tickers: ``BRK.B`` → ``BRK-B`` (Yahoo replaces dots in class
    suffixes with hyphens). Strip whitespace, uppercase, drop footnote
    markers like ``[a]`` that Wikipedia editors sometimes leave behind.
    """
    if not ticker:
        return ""
    cleaned = re.sub(r"\[[^\]]*\]", "", ticker).strip().upper()
    return cleaned.replace(".", "-")


def _normalise_ftse(ticker: str) -> str:
    """LSE tickers: ``RDSA`` → ``RDSA.L``, ``RDS.A`` → ``RDS-A.L``.

    The .L suffix is what Yahoo uses for LSE listings; without it the
    quote lookups land on the (different) NYSE ADR.
    """
    if not ticker:
        return ""
    cleaned = re.sub(r"\[[^\]]*\]", "", ticker).strip().upper()
    # Some Wikipedia tables already include the .L suffix; don't double it.
    if cleaned.endswith(".L"):
        base = cleaned[:-2]
    else:
        base = cleaned
    base = base.replace(".", "-")
    return f"{base}.L" if base else ""


def _normalise_dax(ticker: str) -> str:
    """Xetra tickers: append ``.DE`` for Yahoo (e.g. ``SAP`` → ``SAP.DE``).

    Wikipedia's DAX page lists the Xetra symbol in the ``Ticker`` /
    ``Symbol`` column (e.g. ``ADS``); Yahoo wants ``ADS.DE``.
    """
    if not ticker:
        return ""
    cleaned = re.sub(r"\[[^\]]*\]", "", ticker).strip().upper()
    if cleaned.endswith(".DE"):
        return cleaned
    return f"{cleaned.replace('.', '-')}.DE"


def _normalise_cac(ticker: str) -> str:
    """Euronext Paris tickers: ``.PA`` Yahoo suffix."""
    if not ticker:
        return ""
    cleaned = re.sub(r"\[[^\]]*\]", "", ticker).strip().upper()
    if cleaned.endswith(".PA"):
        return cleaned
    return f"{cleaned.replace('.', '-')}.PA"


def _normalise_nikkei(ticker: str) -> str:
    """Tokyo Stock Exchange tickers: 4-digit code + ``.T`` (e.g.
    ``7203`` → ``7203.T``). Some Wikipedia tables already format the
    number with thousand separators or trailing whitespace — strip
    everything non-digit then re-append the suffix.
    """
    if not ticker:
        return ""
    cleaned = re.sub(r"\[[^\]]*\]", "", ticker).strip()
    digits = re.sub(r"[^0-9]", "", cleaned)
    if not digits:
        return ""
    return f"{digits}.T"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


WIKI_UNIVERSES: dict[str, UniverseDef] = {
    "sp500": UniverseDef(
        url="https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        table_index=0,
        symbol_column="symbol",
        name_column="security",
        sector_column="gics sector",
        industry_column="gics sub-industry",
        normaliser=_normalise_us_dot,
    ),
    "nasdaq100": UniverseDef(
        url="https://en.wikipedia.org/wiki/Nasdaq-100",
        table_index=4,
        symbol_column="ticker",
        name_column="company",
        sector_column="gics sector",
        industry_column="gics sub-industry",
        normaliser=_normalise_us_dot,
    ),
    "sp400_midcap": UniverseDef(
        url="https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
        table_index=0,
        symbol_column="symbol",
        name_column="security",
        sector_column="gics sector",
        industry_column="gics sub-industry",
        normaliser=_normalise_us_dot,
    ),
    "sp600_smallcap": UniverseDef(
        url="https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
        table_index=0,
        symbol_column="symbol",
        name_column="company",
        sector_column="gics sector",
        industry_column="gics sub-industry",
        normaliser=_normalise_us_dot,
    ),
    "ftse100": UniverseDef(
        url="https://en.wikipedia.org/wiki/FTSE_100_Index",
        table_index=4,
        symbol_column="ticker",
        name_column="company",
        sector_column="ftse industry classification benchmark sector",
        industry_column="ftse industry classification benchmark sector",
        normaliser=_normalise_ftse,
    ),
    "dax": UniverseDef(
        url="https://en.wikipedia.org/wiki/DAX",
        table_index=4,
        symbol_column="ticker",
        name_column="company",
        sector_column="prime standard sector",
        industry_column="prime standard sector",
        normaliser=_normalise_dax,
    ),
    "cac40": UniverseDef(
        url="https://en.wikipedia.org/wiki/CAC_40",
        table_index=4,
        symbol_column="ticker",
        name_column="company",
        sector_column="sector",
        industry_column="sector",
        normaliser=_normalise_cac,
    ),
    "nikkei225": UniverseDef(
        url="https://en.wikipedia.org/wiki/Nikkei_225",
        table_index=1,
        symbol_column="code",
        name_column="company",
        sector_column="sector",
        industry_column="sector",
        normaliser=_normalise_nikkei,
    ),
}


# Floor below which a parse is treated as "almost certainly wrong"
# (Wikipedia editors occasionally refactor a page and the table_index
# we used to anchor on stops pointing at the constituents). The CLI
# uses this to mark the universe as errored without clobbering the
# previously good rows in Postgres.
MIN_SYMBOLS_PER_UNIVERSE = 10


# Modest timeout — Wikipedia goes down occasionally, and we'd rather
# fail one universe fast than have launchd starve the whole batch.
DEFAULT_TIMEOUT_S = 20

DEFAULT_USER_AGENT = (
    "tradepro-strategies/0.1 "
    "(+https://github.com/sunnylnct007/tradepro; daily universe refresh)"
)


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


def _http_get(url: str, *, timeout: int = DEFAULT_TIMEOUT_S) -> str:
    """Single-shot HTTP GET with our UA + a short timeout."""
    resp = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html"},
    )
    resp.raise_for_status()
    return resp.text


def _match_column(columns: Iterable[str], wanted: str) -> str | None:
    """Case-insensitive substring match for a column label.

    Wikipedia tables sometimes wrap the column header in a multi-line
    layout that pandas reads as ``"GICS\\nSector"`` — normalise spaces
    and lowercase before comparing.
    """
    wanted_norm = re.sub(r"\s+", " ", wanted.strip().lower())
    for col in columns:
        col_norm = re.sub(r"\s+", " ", str(col).strip().lower())
        if wanted_norm == col_norm or wanted_norm in col_norm:
            return col
    return None


def _select_constituents_table(tables: list, defn: UniverseDef) -> "object":
    """Pick the constituents DataFrame from the candidate list.

    Falls back through a small ladder:

      1. The exact ``table_index`` from the registry definition.
      2. The first table whose columns contain the expected symbol
         column. This handles the common "Wikipedia editor reordered
         the page" case without requiring a code change.
      3. Raise ``UniverseFetchError`` — we won't push garbage to the
         API and won't silently truncate to the wrong table.
    """
    if not tables:
        raise UniverseFetchError("no tables parsed from page")

    # First try the configured index.
    if defn.table_index < len(tables):
        candidate = tables[defn.table_index]
        if _match_column(list(candidate.columns), defn.symbol_column):
            return candidate

    # Scan the rest for one that has the symbol column.
    for i, candidate in enumerate(tables):
        if _match_column(list(candidate.columns), defn.symbol_column):
            log.warning(
                "table_index %d did not have %r; fell back to table %d",
                defn.table_index,
                defn.symbol_column,
                i,
            )
            return candidate

    raise UniverseFetchError(
        f"no table with column matching {defn.symbol_column!r} found "
        f"(scanned {len(tables)} tables)"
    )


def parse_universe_html(html: str, defn: UniverseDef) -> list[Symbol]:
    """Convert a Wikipedia page's HTML into a list of normalised symbols.

    Public so the Behave tests can drive it directly with synthetic
    HTML (no Wikipedia round-trip). The CLI uses ``fetch_universe``,
    which adds the HTTP fetch + the row-count floor check.
    """
    import pandas as pd  # local import — keep module import light

    fetched_at = datetime.now(timezone.utc).isoformat()

    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError as exc:
        # pandas raises ValueError("No tables found") when the HTML
        # has no table tags — fall back to bs4 below so we surface
        # a richer message rather than the bare pandas string.
        if "no tables found" in str(exc).lower():
            tables = _bs4_fallback_tables(html)
            if not tables:
                raise UniverseFetchError("no tables found on page (pandas + bs4)") from exc
        else:
            raise UniverseFetchError(f"pandas.read_html failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — lxml / etree errors bubble up here
        # Try bs4 as a fallback before giving up. Slower, but tolerant
        # of malformed HTML that lxml rejects.
        tables = _bs4_fallback_tables(html)
        if not tables:
            raise UniverseFetchError(
                f"pandas.read_html failed and bs4 fallback found no tables: {exc}",
            ) from exc

    table = _select_constituents_table(tables, defn)
    cols = list(table.columns)
    symbol_col = _match_column(cols, defn.symbol_column)
    name_col = _match_column(cols, defn.name_column)
    sector_col = _match_column(cols, defn.sector_column)
    industry_col = _match_column(cols, defn.industry_column)

    if symbol_col is None:
        raise UniverseFetchError(
            f"symbol column {defn.symbol_column!r} not in {cols!r}",
        )

    rows: list[Symbol] = []
    seen: set[str] = set()
    for _, row in table.iterrows():
        raw_ticker = str(row.get(symbol_col, "")).strip()
        if not raw_ticker or raw_ticker.lower() == "nan":
            continue
        ticker = defn.normaliser(raw_ticker)
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        name = str(row.get(name_col, "")).strip() if name_col else ""
        sector = str(row.get(sector_col, "")).strip() if sector_col else ""
        industry = str(row.get(industry_col, "")).strip() if industry_col else ""
        # pandas turns missing cells into the literal string "nan" — scrub.
        if name.lower() == "nan":
            name = ""
        if sector.lower() == "nan":
            sector = ""
        if industry.lower() == "nan":
            industry = ""
        rows.append(
            Symbol(
                ticker=ticker,
                name=name,
                sector=sector,
                industry=industry,
                source_url=defn.url,
                fetched_at_utc=fetched_at,
            )
        )
    return rows


def _bs4_fallback_tables(html: str) -> list:
    """Minimal pandas-compatible fallback when lxml chokes on the page.

    Returns a list of DataFrames so the rest of the pipeline can stay
    pandas-shaped. BeautifulSoup parses HTML with the more tolerant
    ``html.parser`` so pages with mismatched tags still load.
    """
    try:
        from bs4 import BeautifulSoup  # local import — soft dependency
        import pandas as pd
    except ImportError:  # pragma: no cover — bs4 is in the dep tree
        return []
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for table in soup.find_all("table"):
        try:
            df_list = pd.read_html(io.StringIO(str(table)))
            out.extend(df_list)
        except Exception:  # noqa: BLE001 — best-effort recovery
            continue
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_universe(
    name: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
    html_override: str | None = None,
) -> list[Symbol]:
    """Fetch one universe by registry name.

    ``html_override`` is for tests — pass synthetic HTML in and skip the
    HTTP call entirely. CLI callers should never use it.

    Raises ``UniverseFetchError`` on network OR parse failure; the caller
    decides whether to swallow it (batch mode) or surface it (single
    universe interactive mode).
    """
    if name not in WIKI_UNIVERSES:
        raise UniverseFetchError(
            f"unknown universe {name!r}; "
            f"known: {sorted(WIKI_UNIVERSES)!r}",
        )
    defn = WIKI_UNIVERSES[name]
    if html_override is not None:
        html = html_override
    else:
        try:
            html = _http_get(defn.url, timeout=timeout)
        except requests.RequestException as exc:
            raise UniverseFetchError(f"HTTP fetch failed for {defn.url}: {exc}") from exc
    return parse_universe_html(html, defn)


def fetch_all_universes(
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
    only: list[str] | None = None,
    html_overrides: dict[str, str] | None = None,
) -> dict[str, object]:
    """Scrape every universe in the registry, isolating per-universe errors.

    Return shape::

        {
          "sp500":     [Symbol, ...],
          "nasdaq100": [Symbol, ...],
          ...
          "_errors":   {"ftse100": "HTTP timeout", "dax": "table column missing"}
        }

    The ``_errors`` key is *always* present (even when empty) so callers
    can rely on the shape without doing ``.get``. Errors do NOT raise;
    the contract is "best-effort batch — push what we got".

    ``only`` constrains the scrape to a subset for ad-hoc refreshes; the
    daily launchd job leaves it empty to walk the full registry.

    ``html_overrides`` is for tests: ``{"sp500": "<html>...</html>"}``
    short-circuits the HTTP fetch so the Behave suite never depends on
    Wikipedia being up.
    """
    targets = only or list(WIKI_UNIVERSES)
    out: dict[str, object] = {}
    errors: dict[str, str] = {}
    for name in targets:
        if name not in WIKI_UNIVERSES:
            errors[name] = f"unknown universe (not in registry)"
            continue
        try:
            html_override = (html_overrides or {}).get(name)
            symbols = fetch_universe(
                name,
                timeout=timeout,
                html_override=html_override,
            )
        except UniverseFetchError as exc:
            errors[name] = str(exc)
            log.warning("universe %s failed: %s", name, exc)
            continue
        except Exception as exc:  # noqa: BLE001 — anything from requests/pandas
            errors[name] = f"unexpected error: {exc}"
            log.exception("universe %s raised unexpected exception", name)
            continue

        # Floor check: protects against Wikipedia schema drift silently
        # turning a 500-symbol universe into a 2-row noise table. Mark
        # as error so the CLI / API leave the previous good data in place.
        if len(symbols) < MIN_SYMBOLS_PER_UNIVERSE:
            errors[name] = (
                f"below floor: got {len(symbols)} symbols, "
                f"expected >={MIN_SYMBOLS_PER_UNIVERSE}"
            )
            log.warning("universe %s below floor (%d) — skipping", name, len(symbols))
            continue

        out[name] = symbols
        log.info("universe %s ok: %d symbols", name, len(symbols))
    out["_errors"] = errors
    return out
