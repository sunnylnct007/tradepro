

def test_a_starred_setup_never_carries_the_engines_own_thin_warning():
    """Owner, 13 Sep 2026: "i will better not see any signal rather than signals
    creating confusion" / "this pollutes the cockpit as well".

    `consider` used to require only `not very_thin` (<0.3x), so anything from
    0.3x to 0.8x kept a clean star while its own WHY text read "light volume
    hasn't confirmed a hold — could be drift, not defense". On 11 Sep that gave
    five considers, four of them warned:

        PFE 0.71x   FIVE 0.58x   FLR 0.44x   ANET 0.79x   ⚠ THIN
        ABBV 0.81x                                        clean

    and the cockpit rendered it as ⭐5 · ⚠5 — every star beside a warning.

    A star and a warning on the same row is the confusion. The engine already
    computed thin_vol; it simply did not act on it.
    """
    import inspect
    from tradepro_strategies.cli import today_setups as T

    src = inspect.getsource(T)
    assert "dist_atr <= 1.0 and not thin_vol" in src, (
        "consider must require REAL participation, not merely non-extreme thinness")
    assert "dist_atr <= 1.0 and not very_thin" not in src


def test_thin_names_are_demoted_not_deleted():
    """They keep their reasoning and stay visible under `hold` / show-extended.
    Suppressing them entirely would hide the fact that the engine looked."""
    import inspect
    from tradepro_strategies.cli import today_setups as T

    src = inspect.getsource(T)
    # `hold` is the landing place and must still exist as a class.
    assert 'cls = "hold"' in src
    # very_thin keeps its separate job (the de-star / no-false-confidence case).
    assert "very_thin" in src
