"""Signal ledger — append-only log of every COMPASS / CATALYST signal.

Purpose: build an immutable evidence record so model performance can't
be cherry-picked.  Every signal fired is written immediately; outcomes
are closed in-place when the position resolves.

Storage: JSON-Lines file at ~/.tradepro/signal_ledger.jsonl (one JSON
object per line).  Append-only by convention — never delete a row.
Phase 2: Postgres `signal_events` table, same schema.

Key questions this answers:
  - "Is the model actually working?" — hit rate, expectancy per model
  - "Which symbols does COMPASS call correctly?" — per-symbol stats
  - "Are we firing too many signals?" — frequency by model / universe

Signal lifecycle:
  OPEN   → signal fired, waiting for entry or expiry
  ACTIVE → entry triggered (price touched entry zone)
  CLOSED → one of: HIT_TARGET | STOPPED_OUT | EXPIRED | MANUAL_CLOSE

Usage:
    from tradepro_strategies.signal_ledger import SignalLedger, SignalRecord

    ledger = SignalLedger()

    # Fire a signal
    sig = SignalRecord.new(
        source="COMPASS",
        symbol="ASML",
        universe="us_semis",
        score=78.5,
        conviction="HIGH",
        entry_price=720.0,
        stop_price=695.0,
        target_price=790.0,
        expires_days=5,
    )
    ledger.append(sig)

    # Close it when the position resolves
    ledger.close_signal(sig.signal_id, outcome="HIT_TARGET", exit_price=793.0)

    # Review performance
    stats = ledger.compute_stats()
    print(stats["hit_rate_pct"], stats["expectancy_pct"])
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

_DEFAULT_PATH = Path(os.environ.get(
    "TRADEPRO_SIGNAL_LEDGER",
    Path.home() / ".tradepro" / "signal_ledger.jsonl",
))

_VALID_OUTCOMES = frozenset({"HIT_TARGET", "STOPPED_OUT", "EXPIRED", "MANUAL_CLOSE"})
_VALID_SOURCES = frozenset({"COMPASS", "CATALYST"})
_VALID_STATUSES = frozenset({"OPEN", "ACTIVE", "CLOSED"})


@dataclass
class SignalRecord:
    signal_id: str
    fired_at: str           # ISO datetime UTC
    source: str             # COMPASS | CATALYST
    symbol: str
    universe: str
    score: float | None     # COMPASS 0–100 or None for CATALYST
    conviction: str | None  # HIGH | MEDIUM | LOW
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    expires_at: str | None  # ISO date; None = no expiry
    status: str = "OPEN"   # OPEN | ACTIVE | CLOSED
    # Outcome fields — populated by close_signal()
    outcome: str | None = None      # HIT_TARGET | STOPPED_OUT | EXPIRED | MANUAL_CLOSE
    closed_at: str | None = None
    exit_price: float | None = None
    return_pct: float | None = None
    holding_days: int | None = None
    # Optional metadata for debugging
    notes: str = ""

    @classmethod
    def new(
        cls,
        *,
        source: str,
        symbol: str,
        universe: str,
        score: float | None = None,
        conviction: str | None = None,
        entry_price: float | None = None,
        stop_price: float | None = None,
        target_price: float | None = None,
        expires_days: int | None = 5,
        notes: str = "",
    ) -> "SignalRecord":
        """Factory for new signals. `expires_days` sets the expiry window
        from today; pass None for no expiry (long-term COMPASS signals)."""
        if source not in _VALID_SOURCES:
            raise ValueError(f"source must be one of {_VALID_SOURCES}, got {source!r}")
        expires_at = None
        if expires_days is not None:
            expires_at = (date.today() + timedelta(days=expires_days)).isoformat()
        return cls(
            signal_id=str(uuid.uuid4()),
            fired_at=datetime.now(timezone.utc).isoformat(),
            source=source,
            symbol=symbol.upper(),
            universe=universe,
            score=score,
            conviction=conviction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            expires_at=expires_at,
            notes=notes,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SignalRecord":
        # Forward-compatible: ignore unknown keys so old records load fine
        # after new fields are added.
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def implied_rr(self) -> float | None:
        """Implied risk/reward ratio at entry."""
        if None in (self.entry_price, self.stop_price, self.target_price):
            return None
        risk = abs(self.entry_price - self.stop_price)
        reward = abs(self.target_price - self.entry_price)
        if risk == 0:
            return None
        return round(reward / risk, 2)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None or self.status == "CLOSED":
            return False
        return date.today() > date.fromisoformat(self.expires_at)


class SignalLedger:
    """Append-only ledger backed by a JSON-Lines file.

    Thread-safety: writes use line-atomic appends which are safe for
    single-writer use (one Mac worker process).  For multi-writer setups
    move to Postgres with a sequence primary key.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def append(self, record: SignalRecord) -> None:
        """Write a new signal. Call immediately when COMPASS / CATALYST fires."""
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict()) + "\n")
        _log.info(
            "signal_ledger: fired %s %s %s score=%s",
            record.source, record.symbol, record.signal_id[:8], record.score,
        )

    def close_signal(
        self,
        signal_id: str,
        *,
        outcome: str,
        exit_price: float | None = None,
    ) -> bool:
        """Mark a signal as closed in-place by rewriting the matching line.

        Returns True if the signal was found and updated, False otherwise.
        `outcome` must be one of HIT_TARGET | STOPPED_OUT | EXPIRED | MANUAL_CLOSE.
        """
        if outcome not in _VALID_OUTCOMES:
            raise ValueError(f"outcome must be one of {_VALID_OUTCOMES}, got {outcome!r}")

        records = self.load_all()
        updated = False
        for rec in records:
            if rec.signal_id == signal_id:
                rec.status = "CLOSED"
                rec.outcome = outcome
                rec.closed_at = datetime.now(timezone.utc).isoformat()
                rec.exit_price = exit_price
                if rec.entry_price is not None and exit_price is not None:
                    rec.return_pct = round(
                        (exit_price - rec.entry_price) / rec.entry_price * 100.0, 3
                    )
                fired_dt = datetime.fromisoformat(rec.fired_at.replace("Z", "+00:00"))
                rec.holding_days = (datetime.now(timezone.utc) - fired_dt).days
                updated = True
                break

        if updated:
            self._rewrite(records)
            _log.info("signal_ledger: closed %s outcome=%s", signal_id[:8], outcome)
        else:
            _log.warning("signal_ledger: signal_id %s not found", signal_id[:8])
        return updated

    def expire_stale(self) -> int:
        """Close all OPEN/ACTIVE signals past their expires_at date.
        Returns the count of signals auto-expired. Call at session start."""
        records = self.load_all()
        count = 0
        for rec in records:
            if rec.status != "CLOSED" and rec.is_expired:
                rec.status = "CLOSED"
                rec.outcome = "EXPIRED"
                rec.closed_at = datetime.now(timezone.utc).isoformat()
                count += 1
        if count:
            self._rewrite(records)
            _log.info("signal_ledger: expired %d stale signals", count)
        return count

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def load_all(self) -> list[SignalRecord]:
        """Load every record from the ledger file."""
        if not self._path.exists():
            return []
        records = []
        with self._path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(SignalRecord.from_dict(json.loads(line)))
                except Exception as exc:  # noqa: BLE001
                    _log.warning("signal_ledger: corrupt line %d — skipped: %s", lineno, exc)
        return records

    def load_open(self) -> list[SignalRecord]:
        """All signals that are OPEN or ACTIVE (not yet closed or expired)."""
        return [r for r in self.load_all() if r.status != "CLOSED" and not r.is_expired]

    def load_closed(self) -> list[SignalRecord]:
        return [r for r in self.load_all() if r.status == "CLOSED"]

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def compute_stats(
        self,
        *,
        source: str | None = None,
        symbol: str | None = None,
        lookback_days: int | None = None,
    ) -> dict:
        """Hit rate, expectancy, and counts for closed signals.

        Filter by source ("COMPASS"|"CATALYST"), symbol, or a rolling
        lookback window to see recent model performance separately.
        """
        closed = self.load_closed()

        if source:
            closed = [r for r in closed if r.source == source]
        if symbol:
            closed = [r for r in closed if r.symbol == symbol.upper()]
        if lookback_days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            closed = [
                r for r in closed
                if r.closed_at and
                datetime.fromisoformat(r.closed_at.replace("Z", "+00:00")) >= cutoff
            ]

        if not closed:
            return {
                "total_closed": 0, "winners": 0, "losers": 0,
                "hit_rate_pct": None, "avg_winner_pct": None,
                "avg_loser_pct": None, "expectancy_pct": None,
                "best_pct": None, "worst_pct": None,
            }

        winners = [r for r in closed if r.outcome == "HIT_TARGET"]
        losers = [r for r in closed if r.outcome in ("STOPPED_OUT", "EXPIRED")]
        returns = [r.return_pct for r in closed if r.return_pct is not None]

        avg_win = (
            sum(r.return_pct for r in winners if r.return_pct is not None) / len(winners)
            if winners else None
        )
        avg_loss = (
            sum(r.return_pct for r in losers if r.return_pct is not None) / len(losers)
            if losers else None
        )
        win_rate = len(winners) / len(closed) if closed else None
        expectancy = None
        if win_rate is not None and avg_win is not None and avg_loss is not None:
            expectancy = round(win_rate * avg_win + (1 - win_rate) * avg_loss, 3)

        return {
            "total_closed": len(closed),
            "winners": len(winners),
            "losers": len(losers),
            "open_count": len(self.load_open()),
            "hit_rate_pct": round(win_rate * 100, 1) if win_rate is not None else None,
            "avg_winner_pct": round(avg_win, 3) if avg_win is not None else None,
            "avg_loser_pct": round(avg_loss, 3) if avg_loss is not None else None,
            "expectancy_pct": expectancy,
            "best_pct": round(max(returns), 3) if returns else None,
            "worst_pct": round(min(returns), 3) if returns else None,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rewrite(self, records: list[SignalRecord]) -> None:
        """Rewrite the entire file. Only used for in-place updates (close_signal)."""
        tmp = self._path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec.to_dict()) + "\n")
        tmp.replace(self._path)


__all__ = ["SignalRecord", "SignalLedger"]
