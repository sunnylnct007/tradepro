"""ONE golden bar store, one writer, one read path — enforced.

docs/DATA_ARCHITECTURE.md (ruling, 11 Nov 2026) names every store, writer and
reader of price bars in this repo and gives each a verdict: KEEP or RETIRE.
This test turns the inventory into a gate:

  * any NEW file that touches the Postgres bar table `ibkr_price_bars`,
  * any NEW file that reads or writes the parquet store directly instead of
    through `BarStore.get`,
  * any NEW file that fetches bars straight from yfinance,

fails the suite with a message pointing at the ruling. The allow-lists below
are the 11 Nov inventory, frozen. They may only SHRINK: when a RETIRE item is
deleted, remove it here in the same PR. Adding to an allow-list is the act the
ruling forbids, and that is what a reviewer must refuse.

Why a grep test is right here (and was wrong for dead-code checks): this gate
does not assert that code works, it asserts that a pattern does NOT appear in
new places. That is exactly what text search can see. Five months of fixes
landed beside the store instead of in it because nothing made the build go red
when a fourth writer or a thirtieth direct reader arrived.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RULING = "docs/DATA_ARCHITECTURE.md"

_SKIP_DIRS = {".git", "node_modules", "bin", "obj", ".venv", "__pycache__", "dist", ".terraform"}
_CODE = {".py", ".cs", ".ts", ".tsx", ".sh", ".sql"}


def _code_files():
    for p in REPO.rglob("*"):
        if p.suffix not in _CODE or not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        yield p


def _hits(pattern: re.Pattern[str], *, exclude_self: bool = True) -> set[str]:
    out: set[str] = set()
    me = Path(__file__).resolve()
    for p in _code_files():
        if exclude_self and p == me:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if pattern.search(text):
            out.add(p.relative_to(REPO).as_posix())
    return out


def _assert_frozen(found: set[str], allowed: set[str], what: str) -> None:
    new = sorted(found - allowed)
    assert not new, (
        f"NEW {what} outside the frozen allow-list:\n  " + "\n  ".join(new)
        + f"\n\nThe ruling in {RULING} forbids this. Route through BarStore.get / "
        "GET /api/marketdata/candles, or if this is a genuine exception, say why in "
        f"{RULING} first. Do not add to the allow-list."
    )


# ---------------------------------------------------------------------------
# 1. Postgres `ibkr_price_bars` — RETIRE. Frozen at the 11 Nov inventory.
# ---------------------------------------------------------------------------
IBKR_PRICE_BARS_ALLOWED = {
    # 12 Nov 2026, step 4: every writer and reader is gone. What remains is
    # history — the migrations that created and fixed the table, and the runner
    # comment about its size. The table is dropped after a parity week; this
    # list then shrinks to the migrations alone.
    "backend/TradePro.Api/Data/MigrationRunner.cs",
    "backend/TradePro.Api/db/migrations/057_ibkr_price_bars.sql",
    "backend/TradePro.Api/db/migrations/065_ibkr_volume_lot_fix.sql",
}


def test_no_new_code_touches_the_postgres_bar_table():
    found = _hits(re.compile(r"ibkr_price_bars"))
    _assert_frozen(found, IBKR_PRICE_BARS_ALLOWED, "reference to ibkr_price_bars")


# ---------------------------------------------------------------------------
# 2. Direct parquet access to the bar store — RETIRE; everything goes through
#    BarStore.get. The store package itself and its tests are the writer.
# ---------------------------------------------------------------------------
_DIRECT_PARQUET = re.compile(
    r"(read_parquet\([^)\n]*bar_cache|bar_cache[^\n]*\.parquet|"
    r"glob\([^)\n]*bar_cache|BASE_DIR\s*=.*bar_cache|rglob\([^)\n]*\.parquet)"
)

DIRECT_PARQUET_ALLOWED = {
    # the store's own tooling (first four) is the writer; the rest are RETIRE (step 5)
    "strategies/tradepro_strategies/cli/bar_cache_audit.py",
    "strategies/tradepro_strategies/cli/bar_cache_cli.py",
    "strategies/tradepro_strategies/cli/bar_cache_harvest.py",
    "strategies/tradepro_strategies/cli/bar_cache_scorecard.py",
    "strategies/tradepro_strategies/cli/build_universe.py",
    "strategies/tradepro_strategies/cli/momentum_candidates.py",
    "strategies/tradepro_strategies/cli/swing_candidates.py",
    "strategies/tradepro_strategies/daily_bars.py",
    "strategies/tradepro_strategies/mcp/tools.py",
}


def test_no_new_code_reads_the_parquet_store_directly():
    found = {
        f for f in _hits(_DIRECT_PARQUET)
        if not f.startswith(("strategies/tests/", "strategies/backtests/", "strategies/scripts/",
                             "strategies/features/"))
    }
    _assert_frozen(found, DIRECT_PARQUET_ALLOWED, "direct parquet access to the bar store")


# ---------------------------------------------------------------------------
# 3. Bars straight from yfinance with no store — RETIRE.
# ---------------------------------------------------------------------------
_YF_BARS = re.compile(r"yf\.download\(|yfinance\.download\(|\.history\(\s*(period|start|interval)=")

YF_DIRECT_ALLOWED = {
    # the provider is the store's own fallback; the rest are RETIRE (steps 5–6)
    "strategies/tradepro_strategies/bar_cache/providers/yfinance_provider.py",
    "strategies/tradepro_strategies/cli/index_strangle_alert.py",
    "strategies/tradepro_strategies/cli/index_strangle_close.py",
    "strategies/tradepro_strategies/cli/index_strangle_paper.py",
    "strategies/tradepro_strategies/cli/index_strangle_sim.py",
    "strategies/tradepro_strategies/cli/preearnings_watch.py",
    "strategies/tradepro_strategies/data.py",
    "strategies/tradepro_strategies/live_quote.py",
    "strategies/tradepro_strategies/paper/bar_bus.py",
    "strategies/tradepro_strategies/paper/sources/yfinance.py",
    "strategies/tradepro_strategies/quant_engine/options/chains.py",
}


def test_no_new_code_fetches_bars_straight_from_yfinance():
    found = {
        f for f in _hits(_YF_BARS)
        if f.startswith("strategies/tradepro_strategies/")
    }
    _assert_frozen(found, YF_DIRECT_ALLOWED, "direct yfinance bar fetch")


# ---------------------------------------------------------------------------
# 4. The ruling exists and names the one store.
# ---------------------------------------------------------------------------
def test_the_ruling_is_in_the_repo():
    doc = (REPO / RULING).read_text(encoding="utf-8")
    assert "One golden bar store. One writer. One read path." in doc
    assert "ibkr_price_bars" in doc and "RETIRE" in doc


@pytest.mark.parametrize("name", sorted(
    IBKR_PRICE_BARS_ALLOWED | DIRECT_PARQUET_ALLOWED | YF_DIRECT_ALLOWED))
def test_allow_list_entries_still_exist(name: str):
    """An allow-list entry for a file that no longer exists is a retire step
    that forgot to shrink the list. Shrink it."""
    assert (REPO / name).exists(), f"{name} is gone — remove it from the allow-list"
