"""tradepro-bar-cache-audit — sweep EXISTING parquet partitions for garbage bars.

Why this exists (22 Aug 2026): the parquet store validates every INCOMING
frame at write time (UsEtfPlugin.validate_frame, added 8 Aug — NaN-close +
isolated-spike, frame rejected → next provider). But rows written BEFORE
that guard existed have never passed through any check, and a cache hit is
never re-validated — so a 2020-style phantom bar already on disk sits in
every backtest read forever. The legacy cache.py guard never applies here:
it protects the OTHER store.

Report-only by default: prints every suspect (symbol, partition, timestamp,
reason) and exits 1 if any found. --quarantine rewrites the affected
partitions WITHOUT the bad rows, saving the removed rows + reasons to
~/.tradepro/quarantine/ (inspectable, never silently vanished) and
refreshing the manifest. Quarantine is deliberate, never the default.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

_DEFAULT_BASE = Path.home() / ".tradepro" / "bar_cache"
_QUARANTINE_DIR = Path.home() / ".tradepro" / "quarantine"


def find_garbage(df: pd.DataFrame) -> list[tuple[object, str]]:
    """Per-bar garbage detection — same two checks the write-time guard
    applies (NaN close; isolated >4x / <0.25x spike vs BOTH neighbours),
    but reported per bar instead of rejecting the frame."""
    out: list[tuple[object, str]] = []
    if df.empty or "close" not in df.columns:
        return out
    c = pd.to_numeric(df["close"], errors="coerce")
    for ts in df.index[c.isna()]:
        out.append((ts, "NaN close"))
    if len(df) >= 3:
        prev, nxt = c.shift(1), c.shift(-1)
        spike = (((c > prev * 4) & (c > nxt * 4))
                 | ((c < prev * 0.25) & (c < nxt * 0.25))).fillna(False)
        for ts in df.index[spike]:
            out.append((ts, f"isolated price spike (close={c.loc[ts]:.4g}, "
                            f"prev={prev.loc[ts]:.4g}, next={nxt.loc[ts]:.4g})"))
    # Flat-phantom runs: identical close + zero volume for 5+ sessions —
    # the VLUE-at-2536.93 wrong-contract signature (22 Aug 2026). DAILY
    # spacing only: flat zero-volume runs are ordinary microstructure at
    # intraday resolutions in thin names.
    daily_spaced = (len(df) >= 5
                    and pd.Series(df.index).diff().median() >= pd.Timedelta(hours=20))

    # SCATTERED phantoms + dead partitions (22 Aug 2026). The run-based check
    # below only fires on 5+ CONSECUTIVE flat zero-volume sessions, so a
    # wrong-contract block whose phantoms are INTERLEAVED with traded bars
    # sailed straight through — MTUM/QUAL/USMV/VLUE kept London-listed prices
    # (in pence: 2713, 4448, 1387) across 18 partitions after a purge that
    # reported clean. Credit to the research session for the separating
    # statistic: TOTAL zero-volume-unchanged-close count, not runs
    # (MTUM 31 / QUAL 34 / USMV 26 / VLUE 15 vs STX 1, AMD 1, rest 0).
    # Ratio-based tests were tried and rejected in both directions — they
    # condemned BILL (a real 85% fall) and VIXY (decay is what it does),
    # while any threshold loose enough to spare those cleared MTUM.
    #
    # KNOWN BENIGN PATTERN: a newly-listed thin ETF genuinely prints
    # zero-volume days in its first years (SWDA.L 2010-2012, 108 such bars,
    # on an otherwise smooth 16-year curve). This audit is REPORT-ONLY by
    # design precisely because that judgement needs a human — flagging is
    # cheap, deleting real history is not.
    if daily_spaced and "volume" in df.columns:
        vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        flat_zero = (c == c.shift(1)) & (vol == 0)
        n_phantom = int(flat_zero.sum())
        if n_phantom >= 3:
            for ts in df.index[flat_zero]:
                out.append((ts, f"phantom bar ({n_phantom} in this partition — "
                                f"unchanged close on zero volume)"))
        # A traded US listing never has a median-ZERO-VOLUME month. This is
        # the signature that caught the survivors: the partition is a
        # different instrument's stale quote feed, not a market.
        elif float(vol.median()) == 0:
            out.append((df.index[0], "dead partition (median volume 0 across "
                                     "the month — stale/wrong-contract feed)"))

    if daily_spaced and "volume" in df.columns:
        flat_zero = ((c == c.shift(1))
                     & (pd.to_numeric(df["volume"], errors="coerce").fillna(0) == 0))
        run = 0
        for i, f_ in enumerate(flat_zero.tolist()):
            run = run + 1 if f_ else 0
            if run == 4:      # 5th identical zero-vol session — flag run so far
                for j in range(i - 4, i + 1):
                    out.append((df.index[j], "flat-phantom run (identical close, zero volume)"))
            elif run > 4:
                out.append((df.index[i], "flat-phantom run (identical close, zero volume)"))
    return out


def _load(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    return df


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-bar-cache-audit")
    ap.add_argument("--base-dir", default=str(_DEFAULT_BASE))
    ap.add_argument("--quarantine", action="store_true",
                    help="rewrite affected partitions without the bad rows; "
                         "removed rows are SAVED to ~/.tradepro/quarantine/ "
                         "with reasons (never silently deleted)")
    args = ap.parse_args()

    base = Path(args.base_dir).expanduser()
    parquets = sorted(base.rglob("*.parquet"))
    print(f"bar-cache audit — {len(parquets)} partition file(s) under {base}")
    total_bad = 0
    affected: list[tuple[Path, pd.DataFrame, list[tuple[object, str]]]] = []
    for p in parquets:
        try:
            df = _load(p)
        except Exception as exc:  # noqa: BLE001 — unreadable = loudest finding
            print(f"  ✗ UNREADABLE {p.relative_to(base)}: {exc}")
            total_bad += 1
            continue
        bad = find_garbage(df)
        if bad:
            total_bad += len(bad)
            affected.append((p, df, bad))
            rel = p.relative_to(base)
            for ts, reason in bad:
                print(f"  ✗ {rel}  {str(ts)[:19]}  {reason}")

    def _report(status: str, msg: str) -> None:
        try:
            from tradepro_strategies.run_log import log_run
            log_run("bar-cache", "integrity-audit", status,
                    error=msg if status != "ok" else None, summary=msg)
        except Exception:  # noqa: BLE001
            pass

    if not total_bad:
        print("CLEAN: no garbage bars in any cached partition.")
        _report("ok", f"integrity audit: {len(parquets)} partitions clean")
        return 0

    print(f"\n{total_bad} suspect bar(s) across {len(affected)} partition(s).")
    _report("warn", f"integrity audit: {total_bad} suspect bar(s) across "
                    f"{len(affected)} of {len(parquets)} partition(s) — "
                    f"run tradepro-bar-cache-audit for detail")
    if not args.quarantine:
        print("Report-only (re-run with --quarantine to remove them; removed "
              "rows are preserved under ~/.tradepro/quarantine/).")
        return 1

    _QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for p, df, bad in affected:
        bad_ts = [ts for ts, _ in bad]
        removed = df.loc[df.index.isin(bad_ts)].copy()
        removed["quarantine_reason"] = [r for _, r in bad]
        removed["quarantined_from"] = str(p)
        qpath = _QUARANTINE_DIR / f"{stamp}_{p.parent.parent.name}_{p.stem}.parquet"
        removed.to_parquet(qpath)
        kept = df.loc[~df.index.isin(bad_ts)]
        kept.to_parquet(p)
        print(f"  ♻ {p.relative_to(base)}: removed {len(removed)} row(s) → {qpath.name}")
    try:
        from tradepro_strategies.run_log import log_run
        log_run("bar-cache", "audit-quarantine", "warn",
                error=f"quarantined {total_bad} garbage bar(s) from "
                      f"{len(affected)} partition(s); rows preserved in "
                      f"~/.tradepro/quarantine/")
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
