"""events.py — known events between today and expiry are NAMED, never missed,
and an absence is only "clear" when both stores actually answered."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tradepro_strategies import events as ev
from tradepro_strategies.cli import events_sync

TODAY = date(2026, 11, 10)
FRESH_STORE = {"totalRows": 5000, "symbols": 900,
               "lastUploadUtc": "2099-01-01T00:00:00Z"}  # always fresh for the test


def _fetch(*, earnings=None, store=FRESH_STORE, symbol_rows=None, market_rows=None,
           fail_paths=()):
    """A fake API. `None` for a bucket means that endpoint is unreachable."""
    def f(path, params):
        if any(path.startswith(p) for p in fail_paths):
            return None
        if path.startswith("/api/earnings-calendar/"):
            return {"events": earnings or [], "store": store}
        if path == "/api/catalysts/":
            rows = market_rows if params["symbol"] == ev.MARKET_SYMBOL else symbol_rows
            return None if rows is None else {"catalysts": rows}
        if path.startswith("/api/today-setups/sector-stress/"):
            # A fresh, calm sector-stress artifact: no sector or market sell-off today.
            from tradepro_strategies import sector_stress as ss
            etfs = {e: {"symbol": e, "dd_pct": -1.0, "stressed": False}
                    for e in list(ss.SECTOR_ETF.values()) + list(ss.MARKET_ETFS)}
            return {"artifact": {"as_of_date": date.today().isoformat(), "etfs": etfs,
                                 "stressed": [], "sector_of": {}}}
        raise AssertionError(path)
    return f


MKT = [{"symbol": "MARKET", "kind": "fomc", "occurs_on": "2026-12-09",
        "title": "FOMC", "source": "market_calendar", "severity": "high"},
       {"symbol": "MARKET", "kind": "cpi", "occurs_on": "2027-01-13",
        "title": "CPI", "source": "market_calendar", "severity": "medium"}]


def test_names_every_event_inside_the_window_and_none_outside():
    w = ev.events_between(
        "DELL", TODAY, date(2026, 12, 19), api_base="http://x",
        fetch=_fetch(earnings=[{"report_date": "2026-11-26", "session": "amc"}],
                     symbol_rows=[{"symbol": "DELL", "kind": "ex_dividend",
                                   "occurs_on": "2027-01-20", "title": "ex-div"}],
                     market_rows=MKT))
    assert w.verified and w.gaps == []
    assert [e.short() for e in w.events] == ["DELL earnings 26 Nov", "FOMC decision 9 Dec"]
    assert w.names() == "DELL earnings 26 Nov · FOMC decision 9 Dec"
    assert len(w.high) == 2


def test_a_clear_window_is_only_clear_when_both_stores_answered():
    w = ev.events_between("AAPL", TODAY, TODAY + timedelta(days=20), api_base="http://x",
                          fetch=_fetch(symbol_rows=[], market_rows=MKT))
    assert w.verified and w.events == []
    assert w.names() == "no known events through expiry"


def test_an_empty_market_calendar_is_a_gap_not_a_clear():
    w = ev.events_between("AAPL", TODAY, TODAY + timedelta(days=30), api_base="http://x",
                          fetch=_fetch(symbol_rows=[], market_rows=[]))
    assert not w.verified
    assert any("market calendar is EMPTY" in g for g in w.gaps)
    assert w.names().startswith("could not check events")


def test_a_stale_earnings_store_is_named_as_a_gap():
    stale = {"totalRows": 5000, "symbols": 900, "lastUploadUtc": "2020-01-01T00:00:00Z"}
    w = ev.events_between("AAPL", TODAY, TODAY + timedelta(days=30), api_base="http://x",
                          fetch=_fetch(store=stale, symbol_rows=[], market_rows=MKT))
    assert not w.verified
    assert any("not authoritative" in g for g in w.gaps)


def test_catalysts_api_down_still_names_what_it_could_see():
    w = ev.events_between(
        "DELL", TODAY, date(2026, 12, 19), api_base="http://x",
        fetch=_fetch(earnings=[{"report_date": "2026-11-26"}],
                     symbol_rows=None, market_rows=None))
    assert not w.verified
    assert [e.kind for e in w.events] == ["earnings"]
    assert "DELL earnings 26 Nov" in w.names() and "could not check" in w.names()


def test_same_event_from_two_sources_is_listed_once():
    w = ev.events_between(
        "DELL", TODAY, date(2026, 12, 19), api_base="http://x",
        fetch=_fetch(earnings=[{"report_date": "2026-11-26"}],
                     symbol_rows=[{"symbol": "DELL", "kind": "earnings",
                                   "occurs_on": "2026-11-26", "title": "Q3",
                                   "source": "finnhub_earnings_calendar"}],
                     market_rows=MKT))
    assert sum(1 for e in w.events if e.kind == "earnings") == 1


def test_window_helpers_accept_both_expiry_spellings():
    f = _fetch(symbol_rows=[], market_rows=MKT)
    a = ev.window_for_contract("SPY", "20261218", api_base="http://x", today=TODAY, fetch=f)
    b = ev.window_for_contract("SPY", "2026-12-18", api_base="http://x", today=TODAY, fetch=f)
    c = ev.window_for_dte("SPY", 38, api_base="http://x", today=TODAY, fetch=f)
    assert a.end == b.end == c.end == date(2026, 12, 18)
    assert [e.kind for e in a.events] == ["fomc"]


def test_to_dict_carries_the_one_line_and_the_gaps():
    w = ev.events_between("AAPL", TODAY, TODAY + timedelta(days=30), api_base="http://x",
                          fetch=_fetch(symbol_rows=[], market_rows=[]))
    d = w.to_dict()
    assert d["verified"] is False and d["count"] == 0 and d["gaps"]
    assert d["names"] == w.names()


# ── the seed file and the sync ───────────────────────────────────────────────

def test_seed_file_loads_and_is_not_in_the_past():
    cal = ev.load_market_calendar()
    assert cal, "market_calendar.json is empty"
    assert all(e.scope == ev.MARKET_SYMBOL for e in cal)
    assert all(e.kind in ev.KINDS for e in cal), {e.kind for e in cal} - set(ev.KINDS)
    # The calendar has to reach further than any expiry a lane would propose.
    assert max(e.on for e in cal) >= date.today() + timedelta(days=180), \
        "the seed runs out within six months — add the next year's dates"


def test_seed_file_has_every_fomc_decision_for_2027():
    fomc_2027 = sorted(e.on for e in ev.load_market_calendar()
                       if e.kind == "fomc" and e.on.year == 2027)
    # federalreserve.gov/monetarypolicy/fomccalendars.htm — decision day of each meeting.
    assert fomc_2027 == [date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28),
                         date(2027, 6, 9), date(2027, 7, 28), date(2027, 9, 15),
                         date(2027, 10, 27), date(2027, 12, 8)]


def test_sync_bodies_match_the_api_contract():
    bodies = events_sync.build()
    assert bodies
    for b in bodies:
        assert b["Symbol"] == "MARKET"
        assert b["Source"] == ev.MARKET_CALENDAR_SOURCE
        assert b["Severity"] in ("low", "medium", "high")
        date.fromisoformat(b["OccursOn"])


def test_a_malformed_seed_entry_refuses_to_load(tmp_path):
    p = tmp_path / "cal.json"
    p.write_text('{"events": [{"kind": "fomc", "on": "2027-13-01", "title": "x"}]}')
    with pytest.raises(ValueError, match="bad date"):
        ev.load_market_calendar(p)


# ── the lanes carry the same answer ──────────────────────────────────────────

def test_live_risk_rows_and_mail_name_the_events():
    from tradepro_strategies.cli import live_risk as lr
    positions = [
        {"symbol": "DELL  261219P00420000", "quantity": -2, "costBasisPrice": 9.0,
         "markPrice": 30.0, "unrealizedPnl": -4200.0},
        {"symbol": "ORCL", "quantity": 100, "unrealizedPnl": -500.0},
        {"symbol": "ORCL  261219C00145000", "quantity": -1, "costBasisPrice": 3.0,
         "markPrice": 1.0, "unrealizedPnl": 200.0},
    ]
    seen = []

    def events_for(sym, expiry):
        seen.append((sym, expiry))
        evs = [{"kind": "fomc", "on": "2026-12-09", "short": "FOMC decision 9 Dec",
                "scope": "MARKET", "severity": "high"}]
        if sym == "DELL":
            evs.insert(0, {"kind": "earnings", "on": "2026-11-26",
                           "short": "DELL earnings 26 Nov", "scope": "DELL", "severity": "high"})
        return {"events": evs, "count": len(evs), "high": len(evs), "verified": True,
                "gaps": [], "names": " · ".join(e["short"] for e in evs)}

    lr._spots = lambda syms: {s: 100.0 for s in syms}  # DELL deep ITM -> CLOSE
    art = lr.build(positions, today=date(2026, 11, 10), events_for=events_for)["artifact"]
    assert ("DELL", "2026-12-19") in seen and ("ORCL", "2026-12-19") in seen
    assert art["positions"][0]["events"]["names"] == "DELL earnings 26 Nov · FOMC decision 9 Dec"
    assert art["calls"][0]["events"]["count"] == 1
    # The book-level list is the union, soonest first, each event once.
    assert [e["short"] for e in art["events"]["events"]] == \
        ["DELL earnings 26 Nov", "FOMC decision 9 Dec"]
    text = lr._alert_text(art)
    assert "KNOWN EVENTS BEFORE YOUR EXPIRIES" in text
    assert "FOMC decision 9 Dec" in text
    assert "events before expiry: DELL earnings 26 Nov" in text


def test_live_risk_without_an_events_checker_says_so_instead_of_clear():
    from tradepro_strategies.cli import live_risk as lr
    lr._spots = lambda syms: {s: 500.0 for s in syms}
    art = lr.build([{"symbol": "DELL  261219P00420000", "quantity": -1,
                     "costBasisPrice": 9.0, "markPrice": 8.0}],
                   today=date(2026, 11, 10))["artifact"]
    w = art["positions"][0]["events"]
    assert w["verified"] is False and "not checked" in w["names"]
    assert art["events"]["verified"] is False
    assert "NOT fully checked" in lr._alert_text(art)


def test_get_events_is_on_the_read_only_mcp_surface():
    from tradepro_strategies.mcp.server import build_server
    names = {t.name for t in build_server(read_only=True)._tool_manager.list_tools()}
    assert "get_events" in names
