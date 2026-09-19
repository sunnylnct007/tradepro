"""The movers grid's extra columns, and the one that must stay blank.

Owner, 18 Sep 2026: the strip showed six names and a percentage each — "we can
have a better display of market movers ... in grid format that we can sort.
they can display the current price, 52 week high etc parameters."

A percentage alone is not actionable: +7% into a 52-week high and +7% off the
floor are different trades and the strip could not tell them apart.

THE RISK THIS GUARDS. A name with three months of history has no 52-week high.
Computing max() over whatever came back and labelling it "52w high" would be a
fabricated number that looks exactly like a measured one — the failure mode this
desk has been bitten by repeatedly. It must come back BLANK, with the window
length stated so a reader can see why.
"""
import pandas as pd
import pytest


def _market_movers_source() -> str:
    """The EXACT source of market_movers(), bounded by the parser.

    Not a fixed byte slice. The previous version took src[start:start+6000] and
    silently stopped covering the end of the function the moment it grew — a
    guard that shrinks as the code it guards expands. Same failure shape as the
    source-slice tests that missed the dead retry loop on 17 Sep.
    """
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "tradepro_strategies" / "cli" / "preearnings_watch.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == "market_movers":
            return ast.get_source_segment(src, node) or ""
    raise AssertionError("market_movers() not found")


def _mover_row(closes, volumes=None, sym="TEST", watched=()):
    """Reproduce the per-symbol enrichment from market_movers()."""
    sub = pd.Series(closes).dropna()
    if len(sub) < 2:
        return None
    last = float(sub.iloc[-1])
    row = {"symbol": sym, "last": round(last, 2),
           "chg_pct": round((last / float(sub.iloc[-2]) - 1) * 100, 2),
           "status": ("watch" if sym in watched else "")}
    win = sub.tail(252)
    if len(win) >= 60:
        hi, lo = float(win.max()), float(win.min())
        row["hi_52w"] = round(hi, 2)
        row["lo_52w"] = round(lo, 2)
        row["off_hi_pct"] = round(100 * (last - hi) / hi, 1) if hi else None
        row["range_pos_pct"] = (round(100 * (last - lo) / (hi - lo), 0)
                                if hi > lo else None)
    row["window_sessions"] = int(len(win))
    if volumes is not None:
        vol = pd.Series(volumes).dropna()
        if len(vol) >= 21:
            avg20 = float(vol.iloc[-21:-1].mean())
            if avg20 > 0:
                row["vol_x_20d"] = round(float(vol.iloc[-1]) / avg20, 2)
    return row


def test_a_full_year_gets_every_column():
    closes = list(range(100, 352))          # 252 rising sessions, 100 -> 351
    r = _mover_row(closes)
    assert r["last"] == 351.0
    assert r["hi_52w"] == 351.0
    assert r["lo_52w"] == 100.0
    assert r["off_hi_pct"] == 0.0           # sitting ON the high
    assert r["range_pos_pct"] == 100.0      # top of the range
    assert r["window_sessions"] == 252


def test_a_short_history_leaves_the_52w_columns_BLANK():
    # 40 sessions is not a year. A max() over it is not a 52-week high, and
    # presenting one would be a fabricated number wearing a measured label.
    r = _mover_row(list(range(100, 140)))
    assert "hi_52w" not in r
    assert "lo_52w" not in r
    assert "off_hi_pct" not in r
    assert "range_pos_pct" not in r
    # ...but the REASON is published, so a blank reads as short history and
    # not as a data fault.
    assert r["window_sessions"] == 40


def test_the_same_percentage_is_distinguishable_at_the_high_and_at_the_floor():
    # The whole point of the extra columns. Both names are +5% today.
    at_high = _mover_row([100] * 250 + [200, 210])
    off_low = _mover_row([300] * 200 + list(range(300, 350)) + [100, 105])
    assert at_high["chg_pct"] == off_low["chg_pct"] == 5.0
    assert at_high["range_pos_pct"] == 100.0      # breaking out
    assert off_low["range_pos_pct"] < 10          # scraping the floor
    assert at_high["off_hi_pct"] == 0.0
    assert off_low["off_hi_pct"] < -50


def test_volume_is_measured_against_its_own_20_day_norm():
    # 20 quiet sessions then a burst. The average EXCLUDES today (iloc[-21:-1]),
    # otherwise a huge day dilutes the very baseline it is being judged against.
    r = _mover_row(list(range(100, 200)), volumes=[1_000] * 20 + [3_000])
    assert r["vol_x_20d"] == 3.0


def test_a_flat_year_does_not_divide_by_zero():
    # hi == lo. range_pos is undefined, not 0% and not a crash.
    r = _mover_row([50.0] * 100)
    assert r["hi_52w"] == r["lo_52w"] == 50.0
    assert r["range_pos_pct"] is None


def test_the_producer_publishes_the_fields_the_grid_reads():
    """Source guard: the grid's columns must exist in market_movers()."""
    body = _market_movers_source()
    for field in ("hi_52w", "lo_52w", "off_hi_pct", "range_pos_pct",
                  "vol_x_20d", "window_sessions", "scanned"):
        assert f'"{field}"' in body, f"grid reads {field} but the producer stopped emitting it"


# ── company names, cached lazily ────────────────────────────────────

def test_the_name_lookup_is_capped_so_it_never_bursts():
    """213 names x .info would be 213 requests every 15 minutes."""
    body = _market_movers_source()
    # Only MISSING names are fetched...
    assert "if r[\"symbol\"] not in cache" in body
    # ...capped in COUNT...
    assert "missing[:max(0, budget)]" in body
    # ...and on WALL CLOCK, so a slow vendor cannot stall the watch tick.
    assert "deadline" in body and "_time.monotonic()" in body


def test_names_are_persisted_so_the_cache_fills_once():
    body = _market_movers_source()
    assert 'state["symbol_names"] = cache' in body, (
        "names must persist in state — refetching every cycle is the burst "
        "this design exists to avoid"
    )


def test_an_unnamed_symbol_is_still_a_valid_row():
    """A missing name must never drop or blank the mover itself."""
    r = _mover_row(list(range(100, 200)), sym="ZZZZ")
    assert "name" not in r          # not looked up yet
    assert r["symbol"] == "ZZZZ"    # ...and the row is still complete
    assert r["chg_pct"] is not None
    assert r["hi_52w"] is not None
