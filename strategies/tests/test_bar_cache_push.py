"""The push that ends the two-store split.

8 Sep 2026: the charts read postgres ibkr_price_bars while the nightly harvest
wrote local parquet + S3. DOCN closed 126.69 against 112.47 the session before
and the chart still drew 4 Sep.
"""
import io
import tokenize
from pathlib import Path

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


def test_a_rejected_batch_raises_rather_than_returning_a_count():
    """A push that half-lands and reports success is exactly how the stores
    drifted apart unnoticed."""
    code = _code_only(Path(bar_cache_push.__file__).read_text())
    assert "raise RuntimeError(" in code
    assert "resp.status_code >= 300" in code


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
