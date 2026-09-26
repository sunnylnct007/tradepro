"""The push that ends the two-store split.

8 Sep 2026: the charts read postgres ibkr_price_bars while the nightly harvest
wrote local parquet + S3. DOCN closed 126.69 against 112.47 the session before
and the chart still drew 4 Sep.
"""
import io
import tokenize
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from tradepro_strategies.cli import bar_cache_push


def _code_only(src: str) -> str:
    """Source with COMMENTS AND DOCSTRINGS STRIPPED.

    This file explains the old behaviour in prose, so a plain substring match
    would happily match the description of the bug instead of the code. Three
    tests in this repo have already passed that way.
    """
    out, (lr, lc) = [], (1, 0)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        sr, sc = tok.start
        if sr > lr:
            out.append("\n" * (sr - lr))
            lc = 0
        # PAD THE COLUMN GAP. Joining tokens bare turns `raise RuntimeError`
        # into `raiseRuntimeError`, so a test asserting real code finds nothing
        # and a test asserting a single identifier passes anyway — green for
        # the wrong reason, which is the failure mode this helper exists to
        # avoid.
        if sc > lc:
            out.append(" " * (sc - lc))
        out.append(tok.string)
        lr, lc = tok.end
    return "".join(out)


def test_the_push_reads_disk_and_never_fetches():
    """If it could fetch, a gap here would be filled by a DIFFERENT provider
    than the parquet holds, and the two stores would disagree in a way that
    looks like agreement — worse than the split it replaces."""
    code = _code_only(Path(bar_cache_push.__file__).read_text())
    assert "skip_fetch=True" in code
    assert "allow_partial=True" in code


def test_a_rejected_batch_raises_rather_than_returning_a_count(tmp_path):
    """A push that half-lands and reports success is exactly how the stores
    drifted apart unnoticed.

    ASSERTS THE BEHAVIOUR, NOT THE SOURCE TEXT (26 Sep 2026). This grepped for
    the literal "resp.status_code >= 300". Adding retry split that branch into
    "< 300 -> done" and "< 500 -> raise", so the test failed while the property
    it protects was strengthened. A grep test cannot tell those apart — the
    standing lesson on this desk. It now drives the real function against real
    bars on disk and a stubbed transport.
    """
    import pandas as pd
    import pytest

    part = tmp_path / "us_etf" / "OVV" / "1d"
    part.mkdir(parents=True)
    idx = pd.date_range("2026-09-24", periods=2, freq="B", tz="UTC")
    pd.DataFrame({"open": [1.0, 1.0], "high": [1.0, 1.0], "low": [1.0, 1.0],
                  "close": [1.0, 1.0], "volume": [10, 10],
                  "source": ["ibkr", "ibkr"]}, index=idx
                 ).to_parquet(part / "2026-09.parquet")

    calls = {"n": 0}

    class _Rejected:
        status_code = 422
        text = "bad payload"

        def json(self):
            return {"written": 0}

    def fake_post(url, **kw):
        calls["n"] += 1
        return _Rejected()

    with patch.object(bar_cache_push.requests, "post", side_effect=fake_post), \
         patch.object(bar_cache_push.time, "sleep", lambda *_a, **_k: None):
        with pytest.raises(RuntimeError, match="rejected"):
            bar_cache_push.push_bars(
                base_dir=tmp_path, symbols=["OVV"], asset_class="us_etf",
                resolution="1d",
                start=datetime(2026, 9, 24, tzinfo=UTC),
                end=datetime(2026, 9, 26, tzinfo=UTC),
                api_base="http://api")

    assert calls["n"] == 1, (
        f"a 4xx was POSTed {calls['n']} times — a rejection must not be retried")


def test_an_empty_push_is_a_failure_not_a_success():
    """If the harvest ran, there are bars. Zero written means something is
    wrong upstream, and exiting 0 would let the nightly job stay green while
    the charts sat still."""
    code = _code_only(Path(bar_cache_push.__file__).read_text())
    assert 'return 0 if out["written"] > 0 else 1' in code


def test_the_nightly_harvest_actually_calls_the_push():
    """It is only one store if the SAME job does both. The harvest script used
    to end in `exec`, which replaces the shell — anything appended after it
    would never have run."""
    root = Path(bar_cache_push.__file__).resolve().parents[2]  # -> strategies/
    sh = (root / "scripts" / "bar-cache-harvest-daily.sh").read_text()
    assert "tradepro-bar-cache-push" in sh
    assert "exec \"$UV\" run tradepro-bar-cache-harvest" not in sh
    # And a failed push must fail the JOB, not be logged and shrugged off.
    assert "FATAL: bar push exited" in sh


def test_an_asset_class_folder_is_not_a_ticker(tmp_path):
    """CRYPTO and EVENTS are sibling asset-class folders of us_etf, and both are
    plain uppercase words that pass every shape test. Told apart structurally:
    a SYMBOL directory holds resolutions, an ASSET-CLASS directory holds symbols.
    """
    from tradepro_strategies.universe import harvest_symbols

    (tmp_path / "DOCN" / "1d").mkdir(parents=True)      # a symbol
    (tmp_path / "AAPL" / "5m").mkdir(parents=True)      # a symbol
    (tmp_path / "CRYPTO" / "BTC-USD").mkdir(parents=True)   # an asset class
    (tmp_path / "EVENTS").mkdir()                        # a data folder
    (tmp_path / "EVENTS" / "2026-05-31.jsonl").write_text("{}")

    got = harvest_symbols(tmp_path)
    assert "CRYPTO" not in got, "an asset-class folder was harvested as a ticker"
    assert "EVENTS" not in got
    # And the real ones must survive — a filter that rejects everything passes
    # the two asserts above for the wrong reason.
    assert "DOCN" in got and "AAPL" in got
