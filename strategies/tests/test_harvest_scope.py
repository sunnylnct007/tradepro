from tradepro_strategies.cli import harvest_scope as hs


def test_scope_is_the_harvested_list_deduped_and_sorted():
    art = hs.build(["msft", "AAPL", " MSFT ", ""])
    assert art["symbols"] == ["AAPL", "MSFT"] and art["count"] == 2


def test_an_empty_list_is_refused_not_published():
    assert hs.main(["--symbols", " , "]) == 1


def test_every_options_screen_name_is_harvested():
    """15 Nov: 26 of the options screen's names were not harvested, so it
    priced puts off days-old spots (CRWD's last bar was the 12th)."""
    from tradepro_strategies.universe import WHEEL_SLEEVE, harvest_symbols
    missing = set(WHEEL_SLEEVE) - set(harvest_symbols(None))
    assert not missing, sorted(missing)
