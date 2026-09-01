"""ONE email. Every strategy. Each row with its tier and its freshness.

    uv run tradepro-candidates-digest            # send
    uv run tradepro-candidates-digest --dry-run  # print, send nothing

Owner, 1 Sep 2026: *"as user i dont have to think many screens"* and *"not 2
diff emails"*. Phase 5 of `docs/COHERENT_CANDIDATES_PLAN.md`.

## What this replaces

Four senders mailed this account, on different schedules, with different
universes and no shared idea of what a candidate is:

    screener/daily_run   wheel + swing, 30 hardcoded tickers, a 14-point score
    options_screen       wheel, 82 symbols, 14 risk gates
    index strangle       8 indices
    email_digest         the nightly portfolio digest

Two of them were both called "wheel" and reported 21 eligible and 0 candidates
on the same afternoon. The zero was not even a verdict — that path scored on
snapshot fields it never received.

## Why it is safe to have ONE

Because Phase 3 gave every producer the same record. This file reads
`candidates_v2` and knows nothing about any strategy's private field names — no
adapter, no per-strategy special case. Adding a fifth strategy adds a row here
for free; that is the whole return on the previous phase.

## Two rules it will not bend

**Tier travels with every row.** A candidate from a sleeve whose backtest said
DO NOT FUND must never read like one from a sleeve that passed its gates. The
email states it per row, not in a footnote.

**Freshness is per row.** Producers run on different schedules, so staleness is
a property of the row and not of the email. Anything older than the threshold is
marked, and the reason is given — the desk showed 31-Aug cards at 19:31 on 1 Sep
because freshness was treated as a page-level fact.

A strategy that fails to load is NAMED. Silence about a missing strategy is
indistinguishable from a strategy with nothing to show.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
from types import SimpleNamespace

log = logging.getLogger("tradepro.candidates_digest")

# Older than this and the row is called out. 20h spans an overnight gap without
# flagging a normal pre-open board, and catches anything that missed a session.
STALE_HOURS = float(os.environ.get("TRADEPRO_DIGEST_STALE_HOURS", "20"))

# universe key -> (label, read path). Every one of these publishes the common
# record; nothing here knows a strategy's private shape.
SOURCES: tuple[tuple[str, str], ...] = (
    ("Swing", "/api/today-setups/swing/latest"),
    ("Momentum", "/api/today-setups/momentum/latest"),
    ("Puts", "/api/today-setups/post_earnings_puts/latest"),
    ("Wheel", "/api/today-setups/wheel/latest"),
)


def _age_h(as_of: str | None, now: _dt.datetime) -> float | None:
    if not as_of:
        return None
    try:
        t = _dt.datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=_dt.UTC)
        return (now - t).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None


def gather(base: str, token: str | None, now: _dt.datetime | None = None
           ) -> tuple[list[dict], list[str]]:
    """Every strategy's candidates in the common shape, plus what failed to load."""
    import requests

    now = now or _dt.datetime.now(_dt.UTC)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    rows: list[dict] = []
    problems: list[str] = []

    for label, path in SOURCES:
        try:
            r = requests.get(f"{base.rstrip('/')}{path}", headers=headers, timeout=45)
            if r.status_code != 200:
                problems.append(f"{label}: HTTP {r.status_code}")
                continue
            art = (r.json() or {}).get("artifact") or {}
            v2 = art.get("candidates_v2")
            if v2 is None:
                # Not an error — the producer has not run since Phase 3. Say so
                # rather than reporting the strategy as empty, which would be a
                # different and wrong statement.
                problems.append(f"{label}: no candidates_v2 yet (producer not re-run)")
                continue
            for c in v2:
                if not c.get("eligible", True):
                    continue
                c = dict(c)
                c["_age_h"] = _age_h(c.get("as_of") or art.get("as_of_utc"), now)
                rows.append(c)
        except Exception as exc:  # noqa: BLE001 — one dead strategy must not lose the mail
            problems.append(f"{label}: {str(exc)[:80]}")

    # Group by strategy, rank within it. A sigma and a %/yr are not comparable,
    # so a single cross-strategy ranking would be a number that means nothing.
    rows.sort(key=lambda c: (c.get("strategy") or "",
                             -(c.get("metric") if c.get("metric") is not None else -1e9)))
    return rows, problems


def render(rows: list[dict], problems: list[str], now: _dt.datetime) -> tuple[str, str]:
    """(subject, text). The text IS the email — one monospace block, scannable."""
    n = len(rows)
    strategies = sorted({c.get("strategy") for c in rows if c.get("strategy")})
    subject = (f"[CANDIDATES] {n} across {len(strategies)} "
               f"{'strategy' if len(strategies) == 1 else 'strategies'}"
               f" — {now:%Y-%m-%d}" if n else
               f"[CANDIDATES] none today — {now:%Y-%m-%d}")

    out: list[str] = [
        f"TradePro candidates — {now:%Y-%m-%d %H:%M}Z",
        "",
        "CANDIDATES FOR MANUAL USE. Nothing here is placed automatically.",
        "",
    ]

    # SORT HERE, not only in gather(). The grouping below emits a header when
    # the strategy CHANGES, so unsorted input splits one strategy into two
    # groups — a reader would see "Wheel" twice and reasonably conclude they
    # were different things. Caught by a test that passed rows in arrival order.
    rows = sorted(rows, key=lambda c: (
        c.get("strategy") or "",
        -(c.get("metric") if c.get("metric") is not None else -1e9)))

    if not rows:
        out += [
            "No candidates today.",
            "",
            "That is a VERDICT, not a failure: these strategies fire on a minority",
            "of sessions by design, and a quiet day is the rules working. A day with",
            "nothing is not the same as a day nobody screened — if a strategy failed",
            "to load it is listed below.",
        ]
    else:
        cur = None
        for c in rows:
            if c.get("strategy") != cur:
                cur = c.get("strategy")
                tier = c.get("tier") or "?"
                note = ("passed its pre-registered gates" if tier == "gated"
                        else "NOT proven — for your judgement, not for size")
                out += ["", f"── {cur}  [{tier}] — {note}", ""]
            age = c.get("_age_h")
            stale = ("  ** STALE %.0fh **" % age) if (age is not None and age > STALE_HOURS) else ""
            lvl = (f"{c['level']:.2f} {c.get('level_label') or ''}".strip()
                   if c.get("level") is not None else "—")
            met = (f"{c['metric']:.1f}{c.get('metric_label') or ''}"
                   if c.get("metric") is not None else "—")
            entry = f"{c['entry']:.2f}" if c.get("entry") is not None else "—"
            out.append(f"  {c.get('symbol',''):<7}{c.get('action',''):<10}"
                       f"entry {entry:>10}   {lvl:<16}{met:>9}{stale}")
            if c.get("why"):
                out.append(f"          {c['why'][:96]}")

    if problems:
        out += ["", "COULD NOT LOAD — this is not the same as 'no candidates':", ""]
        out += [f"  · {p}" for p in problems]

    out += [
        "",
        f"Rows older than {STALE_HOURS:.0f}h are marked STALE. Freshness is per ROW:",
        "each strategy publishes on its own schedule.",
        "",
        "Board: http://16.60.201.137/ → Candidates",
    ]
    return subject, "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-candidates-digest")
    ap.add_argument("--dry-run", action="store_true", help="print, send nothing")
    ap.add_argument("--html-out", default=None,
                    help="write the rich body to a file (for eyeballing it)")
    ap.add_argument("--api-base", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from .push_to_api import load_credentials
    base, token = load_credentials()
    base = args.api_base or base

    now = _dt.datetime.now(_dt.UTC)
    rows, problems = gather(base, token, now)
    subject, text = render(rows, problems, now)

    if args.html_out:
        from .candidates_html import build_html
        open(args.html_out, "w").write(build_html(rows, problems, now, STALE_HOURS))
        print(f"wrote {args.html_out} ({len(rows)} rows, {len(problems)} problem(s))")
        return 0

    if args.dry_run:
        print(subject)
        print()
        print(text)
        return 0

    try:
        import json
        from .email_digest import CRED_PATH, send_email
        cfg = json.loads(CRED_PATH.read_text())
        # RICH BODY, plain text as the fallback part. Owner: "shdnt the email be
        # roich as opposed to just a pure text". Both renderings come from the
        # SAME rows, so a client showing plain text loses the charts and nothing
        # else — two renderings that can disagree is the defect this plan exists
        # to remove.
        try:
            from .candidates_html import build_html
            html = build_html(rows, problems, now, STALE_HOURS,
                              with_charts=os.environ.get(
                                  "TRADEPRO_DIGEST_CHARTS", "1").lower()
                              not in ("0", "false", "no", "off"))
        except Exception as exc:  # noqa: BLE001 — never lose the mail over presentation
            log.warning("rich body failed, sending plain text (%s)", str(exc)[:160])
            html = "<pre style=\"font-family:monospace\">" + text.replace("<", "&lt;") + "</pre>"
        send_email(SimpleNamespace(subject=subject, text_body=text,
                                   html_body=html, pdf_bytes=None), cfg)
        log.info("candidates digest sent: %s", subject)
    except Exception as exc:  # noqa: BLE001 — a mail problem must not fail the run
        log.warning("candidates digest NOT sent: %s", str(exc)[:200])
        return 1

    try:
        from ..run_log import log_run
        log_run("candidates-digest", "email", "partial" if problems else "ok",
                error=("; ".join(problems)[:300] if problems else None),
                summary=subject)
    except Exception:  # noqa: BLE001
        pass
    return 0
