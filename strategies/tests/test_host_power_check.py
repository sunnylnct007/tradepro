"""The machine every lane runs on has to stay awake.

Four boards were lost between 19 and 28 Oct 2026 and the cause was diagnosed
three times — a DNS blink, a DNS outage, a monotonic() bug — all downstream of
one fact nothing reported: THE LAPTOP WAS ON BATTERY, sleeping after a minute
idle, at 3% charge.
"""
from tradepro_strategies.cli import desk_check as dc


def _pmset(monkeypatch, text):
    class _R:
        stdout = text
    monkeypatch.setattr(dc.subprocess, "run", lambda *a, **kw: _R())


def test_on_mains_is_ok(monkeypatch):
    _pmset(monkeypatch, "Now drawing from 'AC Power'\n -InternalBattery-0\t100%; charged;")
    (c,) = dc.check_host_power()
    assert c.status == dc.OK


def test_charging_on_battery_reading_is_still_ok(monkeypatch):
    # Plugged in but not yet full still reports 'Battery Power' briefly on some
    # machines; what matters is that it is not draining toward sleep.
    _pmset(monkeypatch, "Now drawing from 'Battery Power'\n -InternalBattery-0\t44%; charging; 1:02")
    (c,) = dc.check_host_power()
    assert c.status == dc.OK


def test_discharging_warns_and_states_the_number(monkeypatch):
    """THE 27 OCT CONDITION. A warning without its number is a mood."""
    _pmset(monkeypatch, "Now drawing from 'Battery Power'\n -InternalBattery-0\t3%; discharging; 0:12")
    (c,) = dc.check_host_power()
    assert c.status == dc.WARN
    assert "3%" in c.detail
    assert "BATTERY" in c.detail
    assert c.fix and "plug" in c.fix.lower()


def test_an_unreadable_power_source_is_unknown_not_ok(monkeypatch):
    # Not knowing whether the host will stay awake is not the same as knowing
    # it will — the distinction this desk keeps having to re-learn.
    def _boom(*a, **kw):
        raise OSError("pmset missing")
    monkeypatch.setattr(dc.subprocess, "run", _boom)
    (c,) = dc.check_host_power()
    assert c.status == dc.UNKNOWN


def test_empty_output_is_unknown_not_ok(monkeypatch):
    _pmset(monkeypatch, "")
    (c,) = dc.check_host_power()
    assert c.status == dc.UNKNOWN


def test_on_mains_but_still_draining_is_NOT_ok(monkeypatch):
    """A FALSE OK IN THE FIRST VERSION, caught minutes after shipping.

    An underpowered adapter leaves the host on mains AND discharging, which
    ends in exactly the same sleep as no adapter — reported as healthy the
    whole way down. Plugged in is not the same as powered.
    """
    _pmset(monkeypatch,
           "Now drawing from 'AC Power'\n -InternalBattery-0\t6%; discharging; 0:20")
    (c,) = dc.check_host_power()
    assert c.status == dc.WARN
    assert "6%" in c.detail and "DISCHARGING" in c.detail


def test_on_mains_and_charging_from_low_is_ok(monkeypatch):
    # The ordinary recovery case: plugged in at 3% and climbing is fine, and
    # must not nag.
    _pmset(monkeypatch,
           "Now drawing from 'AC Power'\n -InternalBattery-0\t3%; charging; (no estimate)")
    (c,) = dc.check_host_power()
    assert c.status == dc.OK


def test_on_mains_at_a_healthy_level_is_ok_even_if_idle(monkeypatch):
    # 'AC attached; not charging' at 95% is normal battery management, not a
    # failing adapter — the low-charge condition is what distinguishes them.
    _pmset(monkeypatch,
           "Now drawing from 'AC Power'\n -InternalBattery-0\t95%; AC attached; not charging")
    (c,) = dc.check_host_power()
    assert c.status == dc.OK
