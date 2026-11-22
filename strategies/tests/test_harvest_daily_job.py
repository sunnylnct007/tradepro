"""The nightly daily-bar chain as one job (22 Nov 2026), for Fargate. Pins:
the Mac script's steps in the Mac script's order; a failed harvest stops the
chain; --no-email drops exactly the mail flag; nothing else changes."""
from __future__ import annotations

import datetime as dt
import sys

import pytest

from tradepro_strategies.cli import harvest_daily as HD


def test_harvest_argv_is_the_mac_scripts_call():
    argv = HD.harvest_argv(["SPY", "QQQ"], dt.date(2026, 11, 22), api_base="http://api.test")
    assert argv[:6] == ["--resolution", "1d", "--asset", "us_etf", "--allow-partial", "--verbose"]
    assert argv[argv.index("--from") + 1] == "2026-11-12"      # trailing 10 days
    assert argv[argv.index("--to") + 1] == "2026-11-22"
    assert argv[argv.index("--symbols") + 1] == "SPY,QQQ"
    assert argv[-2:] == ["--api-base", "http://api.test"]


def test_the_follow_ups_are_the_scripts_in_the_scripts_order():
    names = [n for n, _, _ in HD.FOLLOW_UPS]
    assert names == ["tradepro-harvest-scope", "tradepro-daily-evaluation",
                     "tradepro-shadow-books", "tradepro-sector-stress"]
    assert HD.FOLLOW_UPS[1][2] == ["--push", "--email"]


def test_no_email_drops_only_the_mail_flag():
    quiet = dict((n, a) for n, _, a in HD.follow_ups(email=False, symbols=["SPY"]))
    assert quiet["tradepro-daily-evaluation"] == ["--push"]
    assert quiet["tradepro-shadow-books"] == ["--push"]


def test_harvest_scope_receives_the_harvested_symbols():
    # First Fargate run (22 Nov 2026): "error: the following arguments are
    # required: --symbols", rc 2. The scope is what was harvested, joined.
    args = dict((n, a) for n, _, a in HD.follow_ups(email=True, symbols=["SPY", "QQQ"]))
    assert args["tradepro-harvest-scope"] == ["--symbols", "SPY,QQQ", "--push"]


def test_every_follow_up_argv_parses_against_its_real_cli(monkeypatch):
    """The wrapper must speak each script's actual argument list — a grep of
    the shell script cannot see a required flag. Parse only; nothing runs."""
    import argparse, importlib, inspect
    seen = {}
    real_parse = argparse.ArgumentParser.parse_args
    def spy(self, argv=None, ns=None):
        out = real_parse(self, argv, ns)
        seen["ok"] = True
        raise SystemExit(0)  # parsed cleanly; do not run the body
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", spy)
    for name, module, args in HD.follow_ups(email=True, symbols=["SPY"]):
        seen.clear()
        m = importlib.import_module(module)
        monkeypatch.setattr(sys, "argv", [name, *args])  # for mains that read sys.argv
        call = (lambda: m.main(args)) if inspect.signature(m.main).parameters else m.main
        with pytest.raises(SystemExit) as e:
            call()
        assert e.value.code == 0 and seen.get("ok"), f"{name}: argv {args} rejected by its CLI"


def _wire(monkeypatch, harvest_rc: int):
    calls = []
    def fake_run(name, module, argv):
        calls.append(name)
        return harvest_rc if name == "tradepro-bar-cache-harvest" else 0
    from tradepro_strategies.cli import harvest_5m
    monkeypatch.setattr(harvest_5m, "_run_cli", fake_run)
    monkeypatch.setattr(HD, "scope", lambda: ["SPY"])
    from tradepro_strategies.cli import push_to_api
    monkeypatch.setattr(push_to_api, "load_credentials", lambda: ("http://api.test", "t"))
    return calls


def test_a_failed_harvest_stops_the_chain(monkeypatch, capsys):
    calls = _wire(monkeypatch, harvest_rc=3)
    assert HD.main([]) == 3
    assert calls == ["tradepro-bar-cache-harvest"]
    assert "nothing below it ran" in capsys.readouterr().err


def test_a_good_harvest_runs_every_follow_up(monkeypatch, capsys):
    calls = _wire(monkeypatch, harvest_rc=0)
    assert HD.main(["--no-email"]) == 0
    assert calls == ["tradepro-bar-cache-harvest", "tradepro-harvest-scope",
                     "tradepro-daily-evaluation", "tradepro-shadow-books", "tradepro-sector-stress"]
    assert "all steps ok" in capsys.readouterr().out


def test_an_empty_scope_is_fatal(monkeypatch, capsys):
    _wire(monkeypatch, harvest_rc=0)
    monkeypatch.setattr(HD, "scope", lambda: [])
    assert HD.main([]) == 2
    assert "returned nothing" in capsys.readouterr().err
