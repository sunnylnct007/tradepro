"""The live card is graded against its schedule, and a missed run says WHY.
18 Nov 2026: the 17 Nov run failed (AWS sign-in expired); the card sat two
sessions old and nothing on the desk said so."""
import datetime as dt

from tradepro_strategies.cli import desk_check as D

TZ = dt.timezone.utc


def _at(y, m, d, h, mi):
    return dt.datetime(y, m, d, h, mi, tzinfo=TZ)


def _api(as_of):
    return lambda base, token, path, timeout=30: {"asOfUtc": as_of, "artifact": {}}


def test_a_card_from_the_last_run_is_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "_get", _api("2026-11-17T22:51:00Z"))
    monkeypatch.setattr(D, "LIVE_RISK_LOG_DIR", str(tmp_path))
    [c] = D.check_live_book("b", "t", now_local=_at(2026, 11, 18, 21, 45))
    assert c.status == D.OK


def test_a_missed_run_is_broken_and_names_the_expired_sign_in(monkeypatch, tmp_path):
    (tmp_path / "live-risk-2026-11-17.log").write_text(
        "Error when retrieving token from sso: Token has expired and refresh failed\n"
        "[2026-11-17T22:53:29Z] live-risk FAILED rc=1\n")
    monkeypatch.setattr(D, "_get", _api("2026-11-16T22:50:12Z"))
    monkeypatch.setattr(D, "LIVE_RISK_LOG_DIR", str(tmp_path))
    [c] = D.check_live_book("b", "t", now_local=_at(2026, 11, 18, 21, 45))
    assert c.status == D.BROKEN
    assert "AWS sign-in" in c.detail and "16 Nov" in c.detail and "aws sso login" in c.fix


def test_the_weekend_is_not_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "_get", _api("2026-11-20T22:51:00Z"))      # Friday's run
    monkeypatch.setattr(D, "LIVE_RISK_LOG_DIR", str(tmp_path))
    [c] = D.check_live_book("b", "t", now_local=_at(2026, 11, 23, 20, 0))   # Monday before the run
    assert c.status == D.OK


def test_tonights_failure_shows_at_once_not_after_the_grace(monkeypatch, tmp_path):
    (tmp_path / "live-risk-2026-11-18.log").write_text("Error 403 - Access Denied\n[..] live-risk FAILED rc=1\n")
    monkeypatch.setattr(D, "_get", _api("2026-11-17T22:51:00Z"))
    monkeypatch.setattr(D, "LIVE_RISK_LOG_DIR", str(tmp_path))
    [c] = D.check_live_book("b", "t", now_local=_at(2026, 11, 18, 22, 53))
    assert c.status == D.BROKEN and "403" in c.detail


def test_the_live_risk_plist_is_no_longer_graded_but_the_card_still_is():
    # 23 Nov 2026: live-risk runs on Lambda (22:50 UTC); the Mac plist is
    # unloaded, so grading it would be a false red. The CARD's freshness is
    # still checked by check_live_book (the tests above).
    assert "com.tradepro.live-risk" not in D.SCHEDULED_JOBS
    assert "com.tradepro.trade-alerts" not in D.SCHEDULED_JOBS
    assert "com.tradepro.paper-momentum-rotation-weekly-t212" in D.SCHEDULED_JOBS
