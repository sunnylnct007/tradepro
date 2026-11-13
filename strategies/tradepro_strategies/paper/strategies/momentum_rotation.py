"""Monthly momentum rotation, on paper — the core engine (spec 12 Nov 2026).

WHAT THIS FILE IS. Swing's execution chassis with the rotation's rule. Every
guard the chassis learned the hard way is inherited unchanged: sells checked
against the BROKER first, exits that fail closed when the broker cannot be
read, positions claimed only from this strategy's own OMS fills (every order
is booked under its strategy id), the persisted once-a-day decision lock, the
chase-capped limit entry. Nothing here places an order directly.

WHAT IT DECIDES WITH. `momentum_rotation.decide()` — the SAME `select()` the
backtest runs, so the paper book cannot drift from the tested rule. The
decision fails closed: no bar for the signal day in the store means no new
entries and no rebalance exits; the trailing stop keeps working.

The rule, per the pre-registered gates file:
  * entries: the decision's picks, in the week from the order day (the session
    after the close of the 15th or the last session before it), so a failed
    placement is retried but a mid-cycle start waits for the next decision;
  * exits: a holding that is not in the current picks, from the order day;
    or a close 20% below the highest close since entry (sold next session,
    and that name stays out until the next decision);
  * size: the decision's weight per name x configured capital.

NOT YET (paper v1): trimming kept holdings back to weight, and the account
-15% / -25% drawdown actions. Both are in the backtest; both are owed here.
"""
from __future__ import annotations

import logging

from ..registry import register_strategy
from ...signals.momentum_rotation import RotationSignals
from .mean_reversion_swing import MeanReversionSwingStrategy

_log = logging.getLogger(__name__)


@register_strategy("momentum_rotation")
class MomentumRotationStrategy(MeanReversionSwingStrategy):
    """Swing's engine, the monthly rotation's rule."""

    RULE_LABEL = "momentum rotation"
    MAX_CONCURRENT = 10
    TRAIL = 0.20
    #: Entries are allowed for this many sessions from the order day, so a
    #: placement that fails on the day is retried, but a sleeve started
    #: mid-cycle waits for the next decision instead of buying a stale list.
    ENTRY_WINDOW_DAYS = 7    # calendar days, about five sessions

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.SIGNALS = RotationSignals(self)
        self._cur: str | None = None
        self._today: str | None = None
        self._dec_cache: dict[str, dict] = {}

    # ── the decision ──────────────────────────────────────────────────────
    def _decision(self) -> dict | None:
        """The active monthly decision for today, computed once per process
        per day. It is deterministic from the store, so a restart recomputes
        the same list; nothing is kept in remember(), which does not survive
        a restart."""
        day = self._today
        if not day:
            return None
        if day not in self._dec_cache:
            try:
                import datetime as _dt

                from ...momentum_rotation import decide
                dec = decide(_dt.date.fromisoformat(day))
            except Exception as exc:  # noqa: BLE001 — no decision: no entries, no rebalance exits
                dec = {"error": f"decision failed: {str(exc)[:160]}", "picks": []}
            _log.info("momentum rotation decision for %s: %s", day,
                      dec.get("error") or f"{dec.get('picks')} alloc {dec.get('allocation')}")
            self._dec_cache[day] = dec
        return self._dec_cache[day]

    def _in_window(self, dec: dict) -> bool:
        return bool(dec) and not dec.get("error") and self._today >= str(dec.get("order_date"))

    def _sold_this_cycle(self, dec: dict) -> set[str] | None:
        """Symbols this strategy has SOLD since the decision's order day — read
        from the OMS, because remember()/recall() is in-memory and the daemon
        restarts every 15 minutes: a stopped-out name held only in memory would
        be bought straight back on the next run. Within a cycle a pick is only
        ever sold by the trailing stop, so "sold since the order day" IS
        "stopped out, stays in cash until the next decision". None = the OMS
        could not be read."""
        day = self._today
        if getattr(self, "_sold_cache_day", None) == day:
            return self._sold_cache
        try:
            import requests

            from ...cli.push_to_api import load_credentials
            base, token = load_credentials()
            r = requests.get(f"{base.rstrip('/')}/api/oms/orders",
                             params={"strategyId": self.strategy_id, "limit": 500},
                             headers={"Authorization": f"Bearer {token}"} if token else {},
                             timeout=20)
            r.raise_for_status()
            orders = r.json()
            if isinstance(orders, dict):
                orders = orders.get("orders") or orders.get("items") or []
            since = str(dec.get("order_date"))
            sold = {str(o.get("symbol") or "").split("_")[0].upper() for o in orders
                    if o.get("strategyId") == self.strategy_id
                    and str(o.get("side") or "").upper() == "SELL"
                    and str(o.get("state") or "").upper() not in ("REJECTED", "CANCELLED")
                    and str(o.get("createdAtUtc") or "")[:10] >= since}
        except Exception as exc:  # noqa: BLE001
            _log.warning("could not read this sleeve's sells from the OMS (%s) — "
                         "no entries this run, so a stopped-out name cannot be "
                         "bought back on a guess", str(exc)[:110])
            sold = None
        self._sold_cache_day, self._sold_cache = day, sold
        return sold

    def _wants_entry(self) -> bool:
        import datetime as _dt
        dec = self._decision()
        if not self._in_window(dec) or self._cur not in dec.get("picks", []):
            return False
        opened = _dt.date.fromisoformat(str(dec["order_date"]))
        if _dt.date.fromisoformat(self._today) > opened + _dt.timedelta(days=self.ENTRY_WINDOW_DAYS):
            return False
        sold = self._sold_this_cycle(dec)
        return sold is not None and self._cur not in sold

    def _wants_exit(self, closes, i):
        dec = self._decision()
        if self._in_window(dec) and self._cur not in dec.get("picks", []):
            return True, "rebalance"
        peak = self._peak_since_entry(closes, i)
        if peak and closes[i] <= peak * (1 - self.TRAIL):
            return True, "trailing-stop"
        return False, None

    def _peak_since_entry(self, closes, i) -> float | None:
        start = self._entry_bar.get(self._cur)
        dates = getattr(self, "_dates", None) or []
        if not start or len(dates) != len(closes):
            return None
        since = [c for c, d in zip(closes[: i + 1], dates[: i + 1]) if d >= start]
        return max(since) if since else None

    # ── chassis hooks ─────────────────────────────────────────────────────
    def on_bar(self, bar):  # type: ignore[override]
        self._cur = bar.symbol
        self._today = self._day(bar.timestamp)
        return super().on_bar(bar)

    def _ranked_today(self, bar):  # type: ignore[override]
        """The decision IS the ranking: its picks, best score first."""
        dec = self._decision()
        return list(dec.get("picks", [])) if dec and not dec.get("error") else None

    def _size(self, price: float) -> int:  # type: ignore[override]
        dec = self._decision() or {}
        p = self._p()
        capital = float(p.get("capital") or p.get("start_capital") or 0)
        w = float(dec.get("weight_each") or 0)
        if capital <= 0 or price <= 0 or w <= 0:
            return 0
        return int((capital * w) // price)
