"""Push harvested bars into the store the CHARTS read.

8 Sep 2026. The charts and the strategies were reading DIFFERENT daily-bar
stores, and nothing said so:

    ~/.tradepro/bar_cache + S3   nightly harvest, 21:30 BST (after the close)
                                 -> what the STRATEGIES read
    postgres ibkr_price_bars     the API's own harvester, ~19:59Z (BEFORE it)
                                 -> what the CHARTS read

DOCN closed 126.69 against 112.47 the session before -- a 12.6% day -- and the
desk chart still drew 4 Sep, under a STALE DATA banner nobody could explain.
The harvest had the bar. It had written it to the other store.

This closes the loop: whatever the harvest wrote, push it. ONE writer, once,
after the close.

Reads DISK ONLY (``skip_fetch=True``). This must never become a second
harvester -- if it could fetch, a gap here would be silently papered over with
a different provider's data than the one the parquet holds, and the two stores
would disagree again in a way that looks like agreement.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ..bar_cache.asset_classes import UsEtfPlugin  # noqa: F401 — registers the plugins
from ..bar_cache.store import BarStore

log = logging.getLogger(__name__)

# Big enough that 244 symbols x 10 sessions is a handful of calls, small enough
# that one bad batch is legible in a log rather than a wall of JSON.
BATCH = 500


def _credentials() -> tuple[str | None, str | None]:
    """(api_base, token) from the same file the other jobs use, env winning."""
    base = os.environ.get("TRADEPRO_API_BASE_URL") or os.environ.get("TRADEPRO_API_URL")
    token = os.environ.get("TRADEPRO_API_TOKEN")
    path = Path.home() / ".tradepro" / "credentials"
    if path.exists():
        try:
            d = json.loads(path.read_text())
            base = base or d.get("api_base_url") or d.get("api-base-url")
            token = token or d.get("api_token") or d.get("api-token")
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read %s: %s", path, exc)
    return base, token


def push_bars(
    *,
    base_dir: Path,
    symbols: list[str],
    asset_class: str,
    resolution: str,
    start: datetime,
    end: datetime,
    api_base: str,
    token: str | None = None,
) -> dict:
    """Push what is ON DISK for these symbols. Returns a result summary.

    A symbol with nothing cached is REPORTED, not skipped in silence. The whole
    point of this job is that a store can be quietly behind, and a pusher that
    hides its own gaps would recreate the problem one level up.
    """
    store = BarStore(base_dir=base_dir)
    rows: list[dict] = []
    empty: list[str] = []
    failed: list[str] = []

    for sym in symbols:
        try:
            frame = store.get(
                sym, asset_class, resolution, start, end,
                allow_partial=True,
                # DISK ONLY. See the module docstring: fetching here would let
                # this job disagree with the parquet it is supposed to mirror.
                skip_fetch=True,
                fetched_by="bar_cache_push",
            )
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{sym}: {str(exc)[:80]}")
            continue

        df = getattr(frame, "df", None)
        if df is None or len(df) == 0:
            empty.append(sym)
            continue

        for ts, r in df.iterrows():
            close = r.get("close")
            if close is None or close != close:  # NaN
                continue
            rows.append({
                "symbol": sym,
                "ts": (ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts).isoformat(),
                "open": _num(r.get("open")), "high": _num(r.get("high")),
                "low": _num(r.get("low")), "close": _num(close),
                "volume": int(r["volume"]) if r.get("volume") == r.get("volume")
                          and r.get("volume") is not None else None,
                "source": str(r.get("source") or "bar_cache"),
            })

    written = 0
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{api_base.rstrip('/')}/api/admin/data-trust/bars"
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        resp = requests.post(url, json={"resolution": resolution, "bars": batch},
                             timeout=120, headers=headers)
        if resp.status_code >= 300:
            # FAIL LOUD. A push that half-lands and reports success is exactly
            # how the two stores drifted apart unnoticed for weeks.
            raise RuntimeError(
                f"bar push rejected ({resp.status_code}) on batch {i // BATCH + 1} "
                f"of {(len(rows) + BATCH - 1) // BATCH}: {resp.text[:200]}")
        written += (resp.json() or {}).get("written", 0)

    return {"symbols": len(symbols), "rows": len(rows), "written": written,
            "empty": empty, "failed": failed}


def _num(v):
    try:
        f = float(v)
        return None if f != f else round(f, 6)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", required=True,
                    help="comma-separated, or a path to a file with one per line")
    ap.add_argument("--asset", default="us_etf")
    ap.add_argument("--resolution", default="1d")
    ap.add_argument("--days", type=int, default=10,
                    help="how far back to push; matches the harvest's own window "
                         "so late corrections land")
    ap.add_argument("--base-dir", default=str(Path.home() / ".tradepro" / "bar_cache"))
    ap.add_argument("--api-base", default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    raw = args.symbols
    # A comma means a LIST, always. Calling Path(...).exists() on a 246-symbol
    # string raises OSError [Errno 63] File name too long -- the check meant to
    # be permissive crashed on the ordinary case.
    if "," in raw or len(raw) > 200:
        syms = [s.strip() for s in raw.split(",") if s.strip()]
    else:
        p = Path(raw).expanduser()
        syms = ([s.strip() for s in p.read_text().split() if s.strip()]
                if p.exists() else [raw.strip()])

    base, token = _credentials()
    base = args.api_base or base
    if not base:
        log.error("no API base — set TRADEPRO_API_BASE_URL or pass --api-base. "
                  "NOT pushing, and NOT pretending this succeeded.")
        return 2

    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=args.days + 1)

    out = push_bars(base_dir=Path(args.base_dir).expanduser(), symbols=syms,
                    asset_class=args.asset, resolution=args.resolution,
                    start=start, end=end, api_base=base, token=token)

    log.info("pushed %s row(s) for %s symbol(s) -> %s written",
             out["rows"], out["symbols"], out["written"])
    if out["empty"]:
        log.warning("%s symbol(s) had NOTHING on disk: %s",
                    len(out["empty"]), ", ".join(out["empty"][:15]))
    if out["failed"]:
        log.error("%s symbol(s) could not be read: %s",
                  len(out["failed"]), "; ".join(out["failed"][:10]))
    # An empty push is not a success. If the harvest ran, there are bars.
    return 0 if out["written"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
