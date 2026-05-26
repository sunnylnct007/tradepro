"""Steps for ``wiki_universes.feature``.

Build synthetic HTML in-process and drive
``tradepro_strategies.universes.wikipedia`` against it so no scenario
in this file ever talks to wikipedia.org. The HTML factory below
emits whatever the registered universe definition expects (column
labels are read from the registry so the same builder works for
every universe).
"""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.universes import wikipedia as wu


# ---------------------------------------------------------------------------
# HTML factory
# ---------------------------------------------------------------------------


def _row_html(cells: list[str]) -> str:
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _build_page(headers: list[str], rows: list[list[str]]) -> str:
    """Wrap headers + rows in a barebones HTML page with a single
    <table>. pandas.read_html happily parses this, so we don't need
    the full Wikipedia chrome to exercise the scraper end-to-end.
    """
    head = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    body = "\n".join(_row_html(r) for r in rows)
    return f"""
    <!doctype html>
    <html><body>
      <table class="wikitable">
        <thead>{head}</thead>
        <tbody>{body}</tbody>
      </table>
    </body></html>
    """


def _table_from_context_table(ctx_table) -> tuple[list[str], list[list[str]]]:
    """Pull headers + rows out of a behave gherkin table."""
    headers = list(ctx_table.headings)
    rows = [list(r.cells) for r in ctx_table.rows]
    return headers, rows


def _sp500_html(n_rows: int) -> str:
    """Synthetic S&P 500 page with N filler rows — used for the floor-
    check scenarios where the actual ticker values don't matter, only
    the count.
    """
    headers = ["Symbol", "Security", "GICS Sector", "GICS Sub-Industry"]
    rows: list[list[str]] = []
    for i in range(n_rows):
        rows.append([f"FAKE{i:03d}", f"Fake Co {i}", "Information Technology", "Software"])
    return _build_page(headers, rows)


def _ftse100_html_malformed() -> str:
    """HTML that pandas.read_html can't extract a table from. We give it
    a string with no <table> at all so both the lxml path and the bs4
    fallback come up empty.
    """
    return "<html><body><p>oops, Wikipedia reformatted the page</p></body></html>"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


@given('an inline HTML page with these S&P 500 rows')
def step_sp500_html(context):
    # The feature uses simple column headings ("symbol", "company",
    # "sector") for readability; rewrite them to the labels the
    # sp500 registry entry expects so the matcher hits.
    rename = {
        "symbol": "Symbol",
        "company": "Security",
        "sector": "GICS Sector",
    }
    headers, rows = _table_from_context_table(context.table)
    headers = [rename.get(h.lower(), h) for h in headers]
    context.html = _build_page(headers, rows)
    context.universe_name = None  # set by the @when step


@given('an inline HTML page with these FTSE 100 rows')
def step_ftse_html(context):
    headers, rows = _table_from_context_table(context.table)
    context.html = _build_page(headers, rows)
    context.universe_name = None


@given('inline HTML for "{name}" with {n:d} rows')
def step_inline_html_n_rows(context, name: str, n: int):
    if not hasattr(context, "html_overrides"):
        context.html_overrides = {}
    context.html_overrides[name] = _sp500_html(n)


@given('inline HTML for "{name}" that is malformed')
def step_inline_html_malformed(context, name: str):
    if not hasattr(context, "html_overrides"):
        context.html_overrides = {}
    context.html_overrides[name] = _ftse100_html_malformed()


@when('I parse the page as the "{name}" universe')
def step_parse_as(context, name: str):
    defn = wu.WIKI_UNIVERSES[name]
    context.parsed = wu.parse_universe_html(context.html, defn)


@when('I fetch all universes (only "{a}" and "{b}")')
def step_fetch_all_two(context, a: str, b: str):
    context.batch = wu.fetch_all_universes(
        only=[a, b], html_overrides=context.html_overrides,
    )


@when('I fetch all universes (only "{a}")')
def step_fetch_all_one(context, a: str):
    context.batch = wu.fetch_all_universes(
        only=[a], html_overrides=context.html_overrides,
    )


@then('the parsed symbols include exactly')
def step_assert_exact(context):
    expected_rows = [
        {"ticker": r["ticker"], "sector": r["sector"]}
        for r in [dict(zip(context.table.headings, row.cells)) for row in context.table.rows]
    ]
    got = [{"ticker": s.ticker, "sector": s.sector} for s in context.parsed]
    # Order-insensitive set comparison would lose the count check; do
    # both — same length AND same multiset of (ticker, sector) pairs.
    assert len(got) == len(expected_rows), f"len got={len(got)} expected={len(expected_rows)}: {got!r}"
    got_keys = sorted((r["ticker"], r["sector"]) for r in got)
    exp_keys = sorted((r["ticker"], r["sector"]) for r in expected_rows)
    assert got_keys == exp_keys, f"got={got_keys!r} expected={exp_keys!r}"


@then('the batch result contains "{name}" with at least {n:d} symbols')
def step_batch_has(context, name: str, n: int):
    symbols = context.batch.get(name)
    assert symbols is not None, f"{name} missing from batch: keys={list(context.batch)!r}"
    assert len(symbols) >= n, f"{name} only had {len(symbols)} symbols"


@then('the batch result records an error for "{name}"')
def step_batch_error(context, name: str):
    errors = context.batch.get("_errors") or {}
    assert name in errors, (
        f"expected error for {name}; got errors={errors!r}, keys={list(context.batch)!r}"
    )
    context.last_error_name = name


@then('the batch result _errors dict has {n:d} entry')
@then('the batch result _errors dict has {n:d} entries')
def step_batch_error_count(context, n: int):
    errors = context.batch.get("_errors") or {}
    assert len(errors) == n, f"expected {n} errors, got {len(errors)}: {errors!r}"


@then('the error message for "{name}" contains "{needle}"')
def step_error_contains(context, name: str, needle: str):
    errors = context.batch.get("_errors") or {}
    msg = errors.get(name, "")
    assert needle in msg, f"{needle!r} not in {msg!r}"
