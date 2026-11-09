"""The owner's UI toggles win over the committed seed — and only where set."""
from tradepro_strategies.universe import apply_harvest_overrides


ROWS = [{"symbol": "AAPL", "harvest": True}, {"symbol": "XYZ", "harvest": False},
        {"symbol": "NOFLAG"}]


def test_no_overrides_leaves_rows_untouched():
    assert apply_harvest_overrides(ROWS, None) is ROWS
    assert apply_harvest_overrides(ROWS, {}) is ROWS


def test_exclude_turns_a_seeded_name_off_and_include_turns_one_on():
    out = {r["symbol"]: r.get("harvest") for r in
           apply_harvest_overrides(ROWS, {"AAPL": False, "XYZ": True})}
    assert out == {"AAPL": False, "XYZ": True, "NOFLAG": None}


def test_an_override_for_a_name_not_in_the_file_changes_nothing():
    # The page can INCLUDE any ticker; a name with no bars and no row cannot be
    # conjured into the harvest from here — membership is still the file.
    assert apply_harvest_overrides(ROWS, {"GHOST": True}) == ROWS


def test_tickers_match_case_insensitively():
    out = apply_harvest_overrides([{"symbol": "brk-b", "harvest": True}], {"BRK-B": False})
    assert out[0]["harvest"] is False
