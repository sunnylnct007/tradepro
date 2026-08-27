"""Never ask a provider for a bar that cannot exist yet.

THE DEFECT, 2026-08-27. The daily harvest ran at 08:26 UTC — five hours before
the US open. Every symbol's cache already held bars through 26 Aug, so the only
absent session was TODAY, and delta mode asked ibkr_web for a one-day window:

    ProviderParseError: ibkr_web: 21 bar(s) returned but none within
                        [2026-08-27, 2026-08-27]

IBKR answered correctly — today's daily bar does not exist at 08:26. The chain
walk classified that correct answer as `ibkr_web_parse`, fell through, and
yfinance wrote the partition. The run reported:

    Done: 0 complete  244 partial  0 failed
    Quality: 0 GOLD  7 SILVER  237 BRONZE  0 MISSING

237 of 244 symbols on the fallback, every morning, with nothing in the output
naming the cause. And it was not merely cosmetic: yfinance closes are
DIVIDEND-ADJUSTED while IBKR's are raw, so each morning laid adjusted rows
beside raw history — widening the exact seam ADJ_FACTOR_MIGRATION_PLAN exists
to close, faster than the migration could ever close it.

THE SHAPE, which is the part worth remembering: `_sum_expected_bar_count` had
ALREADY been fixed for the same asymmetry two days earlier. It stopped COUNTING
sessions whose bars cannot be reached. This half kept REQUESTING them. One
piece of the store knew today's bar does not exist yet; the other asked anyway.

The settled test comes from the PLUGIN (`session_close_utc`), not a constant
here: 16:00 ET is 20:00 UTC in summer and 21:00 UTC in winter, and half-days
close at 13:00 ET. A hardcoded 20:00 would be wrong for about a third of the
year, and would be a second definition of a thing the plugin already knows.
"""
from __future__ import annotations

import datetime as dt

import pytest

from tradepro_strategies.bar_cache.asset_classes.us_etf import UsEtfPlugin
from tradepro_strategies.bar_cache.store import BarStore

UTC = dt.timezone.utc


def _utc(y, m, d, h=0):
    return dt.datetime(y, m, d, h, tzinfo=UTC)


# ── the plugin's session close ──────────────────────────────────────────────

def test_session_close_is_dst_aware():
    """The reason this is not a hardcoded 20:00."""
    p = UsEtfPlugin()
    assert p.session_close_utc(dt.date(2026, 8, 27)) == _utc(2026, 8, 27, 20)  # EDT
    assert p.session_close_utc(dt.date(2026, 1, 15)) == _utc(2026, 1, 15, 21)  # EST


def test_half_days_close_early():
    """Black Friday 2026 — 13:00 ET, and in EST, so 18:00 UTC."""
    assert UsEtfPlugin().session_close_utc(dt.date(2026, 11, 27)) == _utc(2026, 11, 27, 18)


def test_a_plugin_that_does_not_model_a_close_returns_none():
    """The base default. A 24h venue has no close, and a plugin that has not
    implemented one must not have a close invented for it."""
    from tradepro_strategies.bar_cache.asset_class import AssetClassPlugin
    assert AssetClassPlugin.session_close_utc(object(), dt.date(2026, 8, 27)) is None


# ── the clamp ───────────────────────────────────────────────────────────────

def _last_settled(start, end, now):
    return BarStore._last_settled_session(UsEtfPlugin(), start, end, now_utc=now)


def test_today_before_the_close_is_not_settled():
    """THE regression. At 08:26 UTC on a trading day, today has not settled, so
    the window collapses and the caller must skip the fetch."""
    got = _last_settled(_utc(2026, 8, 27), _utc(2026, 8, 28), now=_utc(2026, 8, 27, 8))
    assert got == _utc(2026, 8, 27), (
        "with no settled session the helper must return the window start, "
        "so the caller sees an empty range")


def test_today_after_the_close_is_settled():
    """The evening harvest at 20:30 UTC MUST still fetch today's bar — a clamp
    that blocked it would trade one bug for a worse one."""
    got = _last_settled(_utc(2026, 8, 27), _utc(2026, 8, 28), now=_utc(2026, 8, 27, 20))
    assert got == _utc(2026, 8, 28), "midnight AFTER the settled session"


def test_a_window_of_finished_sessions_is_fully_settled():
    got = _last_settled(_utc(2026, 8, 17), _utc(2026, 8, 22), now=_utc(2026, 8, 27, 8))
    assert got == _utc(2026, 8, 22), "midnight after Friday, so Friday is included"


def test_a_weekend_only_window_has_no_sessions():
    """Sat 22 → Sun 23. No sessions at all → None, meaning "cannot tell", and
    the caller leaves the window alone."""
    assert _last_settled(_utc(2026, 8, 22), _utc(2026, 8, 23),
                         now=_utc(2026, 8, 27, 8)) is None


def test_the_end_bound_is_half_open_like_the_fetch_window():
    """`expected_session_dates` is inclusive of the end DATE while a fetch
    window is half-open. A session sitting exactly on the exclusive bound must
    not count — the same off-by-one that produced phantom sessions in
    `_sum_expected_bar_count`, which is why both halves now share the rule.

    Sat 22 → Mon 24 exclusive: Monday is the bound, so there are NO reachable
    sessions and the answer is "cannot tell".
    """
    assert _last_settled(_utc(2026, 8, 22), _utc(2026, 8, 24), now=_utc(2026, 8, 27, 8)) is None


def test_it_never_clamps_a_class_without_a_modelled_close():
    """None from the plugin must propagate as None — not as "settled"."""
    class NoClose(UsEtfPlugin):
        def session_close_utc(self, session_date):
            return None
    got = BarStore._last_settled_session(
        NoClose(), _utc(2026, 8, 17), _utc(2026, 8, 28), now_utc=_utc(2026, 8, 27, 8))
    assert got is None


def test_the_evening_boundary_is_exact():
    """One minute before the close is unsettled; the close itself is settled.
    A daily bar is final AT the close, not a minute later."""
    before = _last_settled(_utc(2026, 8, 27), _utc(2026, 8, 28),
                           now=dt.datetime(2026, 8, 27, 19, 59, tzinfo=UTC))
    after = _last_settled(_utc(2026, 8, 27), _utc(2026, 8, 28), now=_utc(2026, 8, 27, 20))
    assert before == _utc(2026, 8, 27), "19:59 UTC is still mid-session"
    assert after == _utc(2026, 8, 28), "settled at the close -> bound is midnight after"
