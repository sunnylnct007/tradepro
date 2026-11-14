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

    def _own_orders(self) -> list[dict] | None:
        """This strategy's orders from the OMS, read once per run. None = the
        OMS could not be read."""
        if getattr(self, "_orders_cache", "unset") != "unset":
            return self._orders_cache
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
            orders = [o for o in orders if o.get("strategyId") == self.strategy_id]
        except Exception as exc:  # noqa: BLE001
            _log.warning("could not read this sleeve's orders from the OMS (%s)", str(exc)[:110])
            orders = None
        self._orders_cache = orders
        return orders

    @staticmethod
    def _sym(o: dict) -> str:
        return str(o.get("symbol") or "").split("_")[0].upper()

    def _sold_this_cycle(self, dec: dict) -> set[str] | None:
        """Symbols this strategy has SOLD since the decision's order day. Read
        from the OMS, because remember()/recall() is in-memory and the daemon
        restarts every 15 minutes: a stopped-out name held only in memory would
        be bought straight back on the next run. Within a cycle a pick is only
        ever sold by the trailing stop, so "sold since the order day" IS
        "stopped out, stays in cash until the next decision". None = the OMS
        could not be read, and then nothing is bought."""
        orders = self._own_orders()
        if orders is None:
            return None
        since = str(dec.get("order_date"))
        return {self._sym(o) for o in orders
                if str(o.get("side") or "").upper() == "SELL"
                and str(o.get("state") or "").upper() not in ("REJECTED", "CANCELLED")
                and str(o.get("createdAtUtc") or "")[:10] >= since}

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

    # ── the signal board ──────────────────────────────────────────────────
    def board(self) -> dict:
        """BUY / SELL / HOLD for the engine, and what paper execution did about
        each. This is what the cockpit shows: the signal, then whether the
        paper account followed it."""
        import datetime as _dt

        from ...daily_bars import load_bars
        dec = self._board_decision()
        picks = list(dec.get("picks") or [])
        held = sorted(self._fill_price)
        orders = self._own_orders() or []
        since = str(dec.get("order_date") or "")

        def last_order(sym: str, side: str) -> dict | None:
            mine = [o for o in orders if self._sym(o) == sym
                    and str(o.get("side") or "").upper() == side
                    and str(o.get("createdAtUtc") or "")[:10] >= since]
            return max(mine, key=lambda o: str(o.get("createdAtUtc") or "")) if mine else None

        def status(o: dict | None, waiting: str) -> str:
            if not o:
                return waiting
            st = str(o.get("state") or "").upper()
            px = o.get("avgFillPrice") or o.get("limitPrice")
            return f"{st.lower()}" + (f" @ {float(px):.2f}" if px else "")

        def price_info(sym: str) -> dict:
            df = load_bars(sym, "1d", lookback_days=400)
            if df is None:
                return {}
            entry = self._entry_bar.get(sym)
            closes = df["close"].astype(float)
            since_entry = closes[[str(t)[:10] >= (entry or "9999") for t in closes.index]]
            peak = float(since_entry.max()) if len(since_entry) else None
            fill = self._fill_price.get(sym)
            last = float(closes.iloc[-1])
            return {"last": round(last, 2), "entry": round(fill, 2) if fill else None,
                    "pnl_pct": round((last / fill - 1) * 100, 1) if fill else None,
                    "stop": round(peak * (1 - self.TRAIL), 2) if peak else None,
                    "as_of": str(closes.index[-1])[:10]}

        if self._today and self._today < since:
            waiting = f"to buy at the {since} open"
        elif self._today and since and _dt.date.fromisoformat(self._today) > \
                _dt.date.fromisoformat(since) + _dt.timedelta(days=self.ENTRY_WINDOW_DAYS):
            waiting = "entry window closed — not buying a stale list"
        else:
            waiting = "not ordered yet"
        rows = []
        if dec.get("error"):
            pass
        else:
            for k, sym in enumerate(picks, 1):
                if sym in held:
                    continue
                rows.append({"action": "BUY", "symbol": sym, "sector": dec.get("sectors", {}).get(sym),
                             "why": f"rank {k} of 10 by 3/6/12-month momentum (score {dec.get('scores', {}).get(sym)})",
                             "paper": status(last_order(sym, "BUY"), waiting)})
        for sym in held:
            info = price_info(sym)
            if not dec.get("error") and sym not in picks:
                rows.append({"action": "SELL", "symbol": sym, **info,
                             "why": f"no longer in the top 10 on the {dec.get('signal_date')} close",
                             "paper": status(last_order(sym, "SELL"), "sell at the next open")})
            elif info.get("stop") and info.get("last") and info["last"] <= info["stop"]:
                rows.append({"action": "SELL", "symbol": sym, **info,
                             "why": "closed 20% below its highest close since entry (trailing stop)",
                             "paper": status(last_order(sym, "SELL"), "sell at the next open")})
            else:
                rows.append({"action": "HOLD", "symbol": sym, **info,
                             "why": "still in the top 10; exits on the stop or at the next rebalance"})
        return {"strategy": "Momentum rotation (core engine)", "strategy_id": self.strategy_id,
                "signal_date": dec.get("signal_date"), "order_date": dec.get("order_date"),
                "next_signal_date": dec.get("next_signal_date"),
                "market": ("SPY above its 200-day average: invested" if dec.get("market_ok")
                           else "SPY below its 200-day average: cash"),
                "allocation_pct": round(float(dec.get("allocation") or 0) * 100, 1),
                "per_name_pct": round(float(dec.get("weight_each") or 0) * 100, 2),
                "error": dec.get("error"), "rows": rows,
                "generated_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}

    def _board_decision(self) -> dict:
        """The decision the board should SHOW: the next one as soon as its
        signal close is in the store (so on the evening of the 15th it shows
        what will be bought at the next open), otherwise the active one. The
        sleeve still TRADES only the active decision."""
        import datetime as _dt
        active = self._decision() or {}
        try:
            from ...momentum_rotation import _sessions, decide
            today = _dt.date.fromisoformat(self._today)
            nxt = [d for d in _sessions(today, today + _dt.timedelta(days=10)) if d > today]
            if nxt:
                upcoming = decide(nxt[0])
                if not upcoming.get("error") and upcoming.get("signal_date") != active.get("signal_date"):
                    return upcoming
        except Exception as exc:  # noqa: BLE001 — the board falls back to the active decision
            _log.info("no upcoming decision for the board: %s", str(exc)[:100])
        return active

    def on_session_end(self, session_date) -> None:  # type: ignore[override]
        super().on_session_end(session_date)
        if not self._today:
            return
        try:
            import os

            from ...cli import push_to_api as _pta
            base, token = _pta.load_credentials()
            if base:
                _pta.push("today-setups", {"universe": "momentum_rotation", "label": "latest",
                                           "uploaded_by": os.uname().nodename,
                                           "artifact": self.board()},
                          base.rstrip("/"), token)   # default retries ~63 s: outlasts an API redeploy
        except Exception as exc:  # noqa: BLE001 — publishing never breaks a run
            _log.warning("could not publish the momentum signal board: %s", str(exc)[:120])

