"""launchd must run the schedule that is in the repo — or someone must know.

PR #266 fixed option-chain-capture's schedule to 22:15 so it would land inside
Yahoo's post-close window. The merge went green; the INSTALLED plist stayed at
19:00 = 14:00 ET, and the job refused every run for twelve trading days:

    REFUSED: 2026-10-20 14:00 ET is outside the post-close capture window

`check_code_version` cannot catch this — it compares git SHAs, and launchd does
not read the checkout.

THIS CHECK REPORTS, IT DOES NOT SYNC. Neither side is authoritative: the
INSTALLED paper-swing-ibkr carries --wind-down that the repo copy would revert,
while the REPO option-chain-capture was the correct one. A tool that copied
either direction would be wrong a third of the time.
"""
import plistlib

from tradepro_strategies.cli import desk_check as dc


def _plist(path, hour: int, extra: bytes = b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"Label": path.stem, "StartCalendarInterval": [{"Hour": hour, "Minute": 15}]}
    if extra:
        data["Note"] = extra.decode()
    path.write_bytes(plistlib.dumps(data))


def _dirs(monkeypatch, tmp_path):
    repo, agents = tmp_path / "scripts", tmp_path / "LaunchAgents"
    repo.mkdir(parents=True), agents.mkdir(parents=True)
    monkeypatch.setattr(dc, "DEPLOY_SCRIPTS", repo)
    monkeypatch.setattr(dc, "LAUNCH_AGENTS", agents)
    return repo, agents


def test_matching_schedules_are_ok(monkeypatch, tmp_path):
    repo, agents = _dirs(monkeypatch, tmp_path)
    _plist(repo / "com.tradepro.a.plist", 22)
    _plist(agents / "com.tradepro.a.plist", 22)
    (c,) = dc.check_lane_schedules()
    assert c.status == dc.OK and "1 installed" in c.detail


def test_the_october_outage_would_have_been_caught(monkeypatch, tmp_path):
    # The real case: repo 22:15, launchd 19:15.
    repo, agents = _dirs(monkeypatch, tmp_path)
    _plist(repo / "com.tradepro.option-chain-capture.plist", 22)
    _plist(agents / "com.tradepro.option-chain-capture.plist", 19)
    (c,) = dc.check_lane_schedules()
    assert c.status == dc.WARN
    assert "option-chain-capture" in c.detail
    assert c.bad or c.status == dc.WARN


def test_a_warning_states_its_numbers(monkeypatch, tmp_path):
    # Standing rule: a warning that does not state its number is a mood.
    repo, agents = _dirs(monkeypatch, tmp_path)
    for n, (rh, ih) in {"a": (22, 19), "b": (9, 9), "c": (7, 7)}.items():
        _plist(repo / f"com.tradepro.{n}.plist", rh)
        _plist(agents / f"com.tradepro.{n}.plist", ih)
    (c,) = dc.check_lane_schedules()
    assert "1 of 3" in c.detail


def test_a_not_installed_plist_is_not_drift(monkeypatch, tmp_path):
    # 17 repo plists have no installed counterpart and that is usually
    # deliberate (options-screen was retired in favour of screener-daily).
    # Reporting them would make this cry wolf on its first run.
    repo, agents = _dirs(monkeypatch, tmp_path)
    _plist(repo / "com.tradepro.retired.plist", 22)
    _plist(repo / "com.tradepro.live.plist", 22)
    _plist(agents / "com.tradepro.live.plist", 22)
    (c,) = dc.check_lane_schedules()
    assert c.status == dc.OK, c.detail


def test_a_template_is_never_compared(monkeypatch, tmp_path):
    # worker.plist ships with {{PROJECT_DIR}} placeholders. It differs from the
    # installed copy BY DESIGN and must never be reported.
    repo, agents = _dirs(monkeypatch, tmp_path)
    (repo / "com.tradepro.worker.plist").write_bytes(
        plistlib.dumps({"Label": "w", "Dir": "{{PROJECT_DIR}}"}))
    (agents / "com.tradepro.worker.plist").write_bytes(
        plistlib.dumps({"Label": "w", "Dir": "/Users/skumar/tradepro-deploy"}))
    (c,) = dc.check_lane_schedules()
    # No comparable lanes at all -> UNKNOWN, never a false OK and never drift.
    assert c.status == dc.UNKNOWN
    assert "worker" not in c.detail


def test_nothing_comparable_is_unknown_not_ok(monkeypatch, tmp_path):
    # An empty comparison must not read as a clean bill of health.
    _dirs(monkeypatch, tmp_path)
    (c,) = dc.check_lane_schedules()
    assert c.status == dc.UNKNOWN


def test_a_missing_directory_is_unknown_not_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(dc, "DEPLOY_SCRIPTS", tmp_path / "nope")
    monkeypatch.setattr(dc, "LAUNCH_AGENTS", tmp_path / "also-nope")
    (c,) = dc.check_lane_schedules()
    assert c.status == dc.UNKNOWN and not c.status == dc.OK


def test_an_unreadable_plist_is_reported_not_skipped(monkeypatch, tmp_path):
    repo, agents = _dirs(monkeypatch, tmp_path)
    (repo / "com.tradepro.bad.plist").write_bytes(b"not a plist at all")
    (agents / "com.tradepro.bad.plist").write_bytes(b"also not")
    (c,) = dc.check_lane_schedules()
    assert c.status == dc.WARN and "unreadable" in c.detail
