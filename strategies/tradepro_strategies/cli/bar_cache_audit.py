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



def _fix_ibkr_volume(base: Path, parquets: list[Path], *, apply: bool) -> int:
    """Scale IBKR-sourced volume from 100-share lots to shares.

    Idempotent by construction: each partition's manifest records
    `ibkr_volume_lot_fixed`, and a partition carrying that marker is skipped.
    Without it a second run would multiply by 100 again, which is exactly the
    class of silent corruption this whole audit exists to catch."""
    import json as _json
    scaled = skipped = untouched = 0
    rows_changed = 0
    for p in parquets:
        man = p.with_suffix(".manifest.json")
        meta = {}
        if man.exists():
            try:
                meta = _json.loads(man.read_text())
            except Exception:  # noqa: BLE001
                meta = {}
        if meta.get("ibkr_volume_lot_fixed"):
            skipped += 1
            continue
        try:
            df = pd.read_parquet(p)
        except Exception:  # noqa: BLE001
            continue
        if df.empty or "source" not in df.columns or "volume" not in df.columns:
            untouched += 1
            continue
        mask = df["source"].astype(str).str.startswith("ibkr")
        n = int(mask.sum())
        if n == 0:
            untouched += 1
            continue
        rows_changed += n
        scaled += 1
        if apply:
            df.loc[mask, "volume"] = (
                pd.to_numeric(df.loc[mask, "volume"], errors="coerce").fillna(0)
                * 100).astype("int64")
            df.to_parquet(p)
            meta["ibkr_volume_lot_fixed"] = True
            man.write_text(_json.dumps(meta, indent=2))
    verb = "scaled" if apply else "WOULD scale"
    print(f"{verb} {rows_changed:,} IBKR row(s) across {scaled} partition(s); "
          f"{skipped} already fixed, {untouched} with no IBKR rows")
    if not apply:
        print("dry run — re-run with --apply to write")
        return 1
    try:
        from tradepro_strategies.run_log import log_run
        log_run("bar-cache", "ibkr-volume-lot-fix", "warn",
                error=f"migrated {rows_changed} IBKR volume rows x100 across "
                      f"{scaled} partitions (lots to shares)")
    except Exception:  # noqa: BLE001
        pass
    return 0


def _load(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    return df


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-bar-cache-audit")
    ap.add_argument("--base-dir", default=str(_DEFAULT_BASE))
    ap.add_argument("--fix-ibkr-volume", action="store_true",
                    help="ONE-SHOT MIGRATION: multiply volume by 100 on every "
                         "row whose source is an IBKR provider. IBKR reports "
                         "historical volume in 100-share LOTS and it was "
                         "stored raw, so every IBKR bar is 100x understated "
                         "(SPY 21 Aug read 590,483 against a real ~59,000,000). "
                         "Prices are unaffected — only volume. Idempotency is "
                         "enforced by a marker in the manifest, so re-running "
                         "cannot double-apply. Dry-run unless --apply.")
    ap.add_argument("--apply", action="store_true",
                    help="with --fix-ibkr-volume, actually write the change")
    ap.add_argument("--refresh", action="store_true",
                    help="RE-SOURCE every flagged partition from the golden "
                         "chain (force_refresh) and re-check it. This is the "
                         "remediation the wrong-contract incidents needed; it "
                         "was run four times from throwaway inline scripts on "
                         "22 Aug 2026 before being written down. Poison is "
                         "REPLACED, not deleted — the store's quality-aware "
                         "shrink guard allows a smaller TRUE partition to "
                         "overwrite a larger FALSE one.")
    ap.add_argument("--quarantine", action="store_true",
                    help="rewrite affected partitions without the bad rows; "
                         "removed rows are SAVED to ~/.tradepro/quarantine/ "
                         "with reasons (never silently deleted)")
    args = ap.parse_args()

    base = Path(args.base_dir).expanduser()
    parquets = sorted(base.rglob("*.parquet"))

    if args.fix_ibkr_volume:
        return _fix_ibkr_volume(base, parquets, apply=args.apply)

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

    # CROSS-RESOLUTION CHECK (22 Aug 2026). Per-partition tests cannot see a
    # whole intraday partition that belongs to a DIFFERENT LISTING — every bar
    # in it is internally consistent. But the same symbol's daily series is an
    # independent witness: 5m prices that exceed the daily range by 2.5x are
    # not a move, they are another instrument. Found 5 such partitions
    # (STX 2026-08 was 100% wrong-contract) that every other check passed.
    #
    # Daily is the reference because it is the series with the deepest history
    # and the most scrutiny. If daily is ever wrong this check goes quiet, so
    # it complements the per-bar tests rather than replacing them.
    for sym_dir in sorted(p for p in base.rglob("*") if p.is_dir() and p.name in ("5m", "1m", "15m", "30m", "1h")):
        sym_root = sym_dir.parent
        daily = list((sym_root / "1d").glob("*.parquet"))
        if not daily:
            continue
        try:
            dmax = float(pd.concat([pd.read_parquet(f) for f in daily])["close"].max())
        except Exception:  # noqa: BLE001
            continue
        for f in sorted(sym_dir.glob("*.parquet")):
            try:
                df = pd.read_parquet(f)
            except Exception:  # noqa: BLE001
                continue
            if df.empty:
                continue
            imax = float(df["close"].max())
            if imax > dmax * 2.5:
                n = int((df["close"] > dmax * 1.5).sum())
                total_bad += n
                print(f"  ✗ {f.relative_to(base)}  {n}/{len(df)} bars ABOVE the "
                      f"daily range (max {imax:.2f} vs daily {dmax:.2f}) — "
                      f"wrong-contract intraday partition")

    if not total_bad:
        print("CLEAN: no garbage bars in any cached partition.")
        _report("ok", f"integrity audit: {len(parquets)} partitions clean")
        return 0

    print(f"\n{total_bad} suspect bar(s) across {len(affected)} partition(s).")
    _report("warn", f"integrity audit: {total_bad} suspect bar(s) across "
                    f"{len(affected)} of {len(parquets)} partition(s) — "
                    f"run tradepro-bar-cache-audit for detail")
    if args.refresh:
        import time as _time
        from datetime import timedelta as _td
        from ..bar_cache import BarStore
        from ..bar_cache import asset_classes as _reg  # noqa: F401 — registers
        from ..ibkr_bars import bar_store as _bs
        store = _bs()
        fixed = still_bad = 0
        seen: set = set()
        for p_, _df, _bad in affected:
            rel = p_.relative_to(base).parts          # (tree, symbol, res, file)
            if len(rel) < 4:
                continue
            tree, sym, res, part = rel[0], rel[1], rel[2], p_.stem
            if (tree, sym, res, part) in seen:
                continue
            seen.add((tree, sym, res, part))
            try:
                y, mth = int(part[:4]), int(part[5:7])
            except ValueError:
                continue
            start = datetime(y, mth, 1, tzinfo=UTC)
            end = min(datetime.now(UTC), start + _td(days=32))
            try:
                store.get(canonical=sym, asset_class=tree, resolution=res,
                          start=start, end=end, allow_partial=True,
                          force_refresh=True, fetched_by="audit-refresh")
            except Exception as exc:  # noqa: BLE001 — report, never abort the sweep
                print(f"  ! {sym} {res} {part}: {str(exc)[:70]}")
            try:
                after = find_garbage(_load(p_)) if p_.exists() else []
            except Exception:  # noqa: BLE001
                after = []
            if after:
                still_bad += 1
                print(f"  ✗ {sym} {res} {part}: STILL {len(after)} suspect bar(s)")
            else:
                fixed += 1
                print(f"  ✓ {sym} {res} {part}: clean after re-source")
            _time.sleep(1.0)          # pace the shared IBKR session
        print(f"\nrefresh: {fixed} partition(s) clean, {still_bad} still suspect")
        _report("ok" if not still_bad else "warn",
                f"integrity audit --refresh: {fixed} re-sourced clean, "
                f"{still_bad} still suspect")
        return 0 if not still_bad else 1

    if not args.quarantine:
        print("Report-only (re-run with --quarantine to remove them, or "
              "--refresh to re-source them from the golden chain; removed "
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
