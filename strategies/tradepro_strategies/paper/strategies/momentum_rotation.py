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
    #: A weekly or two-week book trades a list that is replaced within days, so
    #: its retry window is the order day and the day after: a lane started
    #: mid-week waits for the next decision rather than buying last week's.
    SHORT_CYCLE_ENTRY_WINDOW_DAYS = 1

    def _every(self) -> str:
        """monthly (the live rule) | biweekly | weekly — the lane's
        --rebalance-every. The decision, the entry window and the board's name
        follow it; every other rule is the same engine."""
        return str(self._p().get("rebalance_every") or "monthly")

    def _rot_params(self):
        from ...momentum_rotation import with_overrides
        return with_overrides(rebalance_every=self._every())

    @property
    def board_name(self) -> str:
        """The monthly board keeps its name; another schedule publishes its own,
        so two books never overwrite one board."""
        e = self._every()
        return "momentum_rotation" if e == "monthly" else f"momentum_rotation_{e}"

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
                dec = decide(_dt.date.fromisoformat(day), self._rot_params())
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
        window = (self.ENTRY_WINDOW_DAYS if self._every() == "monthly"
                  else self.SHORT_CYCLE_ENTRY_WINDOW_DAYS)
        if _dt.date.fromisoformat(self._today) > opened + _dt.timedelta(days=window):
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
        return [self._at_market(o) for o in super().on_bar(bar)]

    @staticmethod
    def _at_market(order):
        """ENTRIES GO AT MARKET, as the backtest assumes (14 Nov 2026).

        The chassis sends swing's entry: a DAY limit at the signal close +1.5%
        (swing_entry_max_chase_pct), an anti-chase cap built for buying DIPS.
        For a momentum rotation it is the wrong rule. The picks are the names
        that have been rising, the backtest buys them at the next open at
        whatever price, and any pick opening more than 1.5% above Friday's
        close would simply not fill — paper quietly diverging from the
        strategy it exists to test, on exactly the strongest names. The
        pullback sleeve's own record on this chassis: 5 of 31 limit buys
        expired unfilled, and those were dip entries.

        Exits are already MARKET. Sizing still uses the decision's weight.
        """
        import dataclasses

        from ..strategy import OrderSide, OrderType
        if order.side == OrderSide.BUY and order.type == OrderType.LIMIT:
            return dataclasses.replace(
                order, type=OrderType.MARKET, limit_price=None,
                tag=f"{order.tag} · at market (rotation buys at the open, like the backtest)")
        return order

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
            return pick_order_to_show(mine)

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
            fill = self._fill_price.get(sym)
            # The peak includes the BUY PRICE: on the day of the buy there is no
            # close since entry yet, and the stop read blank on all ten names.
            highs = [float(x) for x in since_entry.tolist()] + ([float(fill)] if fill else [])
            peak = max(highs) if highs else None
            last = float(closes.iloc[-1])
            last_day = str(closes.index[-1])[:10]
            stop = round(peak * (1 - self.TRAIL), 2) if peak else None
            qty = float(self.position_for(sym).quantity or 0)
            out = {"last": round(last, 2), "entry": round(fill, 2) if fill else None,
                   "qty": qty or None, "stop": stop, "as_of": last_day}
            # P&L = shares x (price NOW - the price we were filled at) (16 Nov
            # 2026). It used to be the last SETTLED close against the fill, and
            # on the day of the buy that close is the one BEFORE we bought: MU
            # read +8.0% while it was +0.5%, DELL +5.8% while it was -1.6%. A
            # settled close predating the fill is never a P&L price.
            lv = live.get(sym)
            now, now_src = None, None
            if lv:
                now, now_src = float(lv), "live"
            elif entry and last_day >= str(entry)[:10]:
                now, now_src = last, f"close {last_day}"
            if fill and now:
                out["now"] = round(now, 2)
                out["now_source"] = now_src
                out["pnl_pct"] = round((now / fill - 1) * 100, 2)
                if qty:
                    out["cost_usd"] = round(qty * fill, 2)
                    out["value_usd"] = round(qty * now, 2)
                    out["pnl_usd"] = round(qty * (now - fill), 2)
            elif fill:
                out["pnl_note"] = "no price since the buy yet"
            # TREND, from the same settled closes (owner, 16 Nov: the old
            # positions table showed whether each stock's trend was green).
            # 30 closes for the sparkline, the 30-session change, and where the
            # price sits against its 50-day average. Settled bars only, so the
            # trend never mixes in an intraday print; `now` stays separate.
            tail = [round(float(x), 2) for x in closes.tail(30).tolist()]
            if len(tail) >= 2:
                out["spark"] = tail
                out["trend_30d_pct"] = round((tail[-1] / tail[0] - 1) * 100, 1)
            if len(closes) >= 50:
                sma50 = float(closes.tail(50).mean())
                out["sma50"] = round(sma50, 2)
                out["vs_sma50_pct"] = round((last / sma50 - 1) * 100, 1)
            # TODAY, from the harvested 5-minute bars (owner, 16 Nov: "the trend
            # can be refreshed with 5 min candle"). DISPLAY ONLY — the strategy
            # decides on daily closes and never reads this. The latest session
            # in the 5m store, its closes for a sparkline, and the move against
            # the previous settled daily close; stamped with the last bar's time
            # so an old intraday line can never pass for a current one.
            try:
                m5 = load_bars(sym, "5m", lookback_days=5)
                if m5 is not None and len(m5):
                    et = m5.index.tz_convert("America/New_York")
                    session = et[-1].date()
                    today_bars = m5[[d == session for d in et.date]]
                    prior = closes[[str(t)[:10] < str(session) for t in closes.index]]
                    if str(session) != _live_session(closes):
                        today_bars = today_bars.iloc[0:0]  # a settled session is not "today"
                    if len(today_bars) >= 2 and len(prior):
                        tc = [round(float(x), 2) for x in today_bars["close"].tolist()]
                        out["spark_today"] = tc
                        out["today_pct"] = round((tc[-1] / float(prior.iloc[-1]) - 1) * 100, 2)
                        out["today_as_of"] = today_bars.index[-1].isoformat()
            except Exception:  # noqa: BLE001 — the intraday line is extra; the row stands without it
                pass
            # INTRADAY WATCH: the rule sells on a CLOSE below the stop, so a live
            # price below it is a warning, not a sale.
            if lv:
                out["live"] = round(float(lv), 2)
                if stop and lv <= stop:
                    out["intraday"] = (f"trading below its stop ({float(lv):.2f} < {stop:.2f}) — "
                                       "sells at the next open only if it CLOSES below")
            return out

        live: dict[str, float] = {}
        if held:
            try:
                from ...live_quote import live_prices
                live = live_prices(held) or {}
            except Exception:  # noqa: BLE001 — no live quote: the settled figures stand
                live = {}
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
        # THE BOOK IN DOLLARS: what we paid, what it is worth now, and the
        # difference — only over names that HAVE a current price, and the count
        # of those that do not, so a partial total is never shown as the whole.
        priced = [r for r in rows if r.get("pnl_usd") is not None]
        book = None
        if held:
            cost = sum(r["cost_usd"] for r in priced)
            book = {"names_held": len(held), "names_priced": len(priced),
                    "cost_usd": round(cost, 2),
                    "value_usd": round(sum(r["value_usd"] for r in priced), 2),
                    "pnl_usd": round(sum(r["pnl_usd"] for r in priced), 2),
                    "pnl_pct": round(sum(r["pnl_usd"] for r in priced) / cost * 100, 2) if cost else None,
                    "up": sum(1 for r in priced if r["pnl_usd"] > 0),
                    "down": sum(1 for r in priced if r["pnl_usd"] < 0)}
        # MARKET AND SECTOR GAUGES (owner, 16 Nov 2026, on a tech sell-off with
        # AMD still held: "harvest SMH and monitor that"). Watched, not traded:
        # the engine's only market rule is SPY vs its 200-day at a decision.
        # Settled daily closes, plus today's move from 5-minute bars when the
        # store has them; a gauge with no bars says so.
        gauges = []
        try:
            from ...universe import MARKET_GAUGES
            for g in MARKET_GAUGES:
                gd = load_bars(g, "1d", lookback_days=400)
                if gd is None or len(gd) < 30:
                    gauges.append({"symbol": g, "error": "no daily bars yet"})
                    continue
                gc = gd["close"].astype(float)
                gl = float(gc.iloc[-1])
                row = {"symbol": g, "last": round(gl, 2), "as_of": str(gc.index[-1])[:10],
                       "chg_1m_pct": round((gl / float(gc.iloc[-22]) - 1) * 100, 1) if len(gc) > 22 else None,
                       "chg_3m_pct": round((gl / float(gc.iloc[-64]) - 1) * 100, 1) if len(gc) > 64 else None,
                       "from_high_pct": round((gl / float(gc.tail(252).max()) - 1) * 100, 1)}
                for n in (50, 200):
                    if len(gc) >= n:
                        row[f"vs_sma{n}_pct"] = round((gl / float(gc.tail(n).mean()) - 1) * 100, 1)
                try:
                    m5 = load_bars(g, "5m", lookback_days=5)
                    if m5 is not None and len(m5):
                        et = m5.index.tz_convert("America/New_York")
                        sess = et[-1].date()
                        tb = m5[[d == sess for d in et.date]]
                        prior = gc[[str(t)[:10] < str(sess) for t in gc.index]]
                        if str(sess) != _live_session(gc):
                            tb = tb.iloc[0:0]  # a settled session is not "today"
                        if len(tb) and len(prior):
                            row["today_pct"] = round((float(tb["close"].iloc[-1]) / float(prior.iloc[-1]) - 1) * 100, 2)
                            row["today_as_of"] = tb.index[-1].isoformat()
                except Exception:  # noqa: BLE001 — today's move is extra
                    pass
                gauges.append(row)
        except Exception as exc:  # noqa: BLE001 — the gauges are extra; the board stands without them
            _log.info("no market gauges for the board: %s", str(exc)[:100])
        today = None
        try:
            from ...momentum_rotation import ranking_today
            tr = ranking_today()
            cur = set(picks)
            today = {"as_of": tr.get("date"), "picks": tr.get("picks", []),
                     "scores": tr.get("scores", {}), "market_ok": tr.get("market_ok"),
                     "entering": [x for x in tr.get("picks", []) if x not in cur],
                     "leaving": [x for x in picks if x not in set(tr.get("picks", []))]}
        except Exception as exc:  # noqa: BLE001 — the daily view is extra; the board stands without it
            _log.info("no daily ranking for the board: %s", str(exc)[:100])
        # THE INPUTS, STATED. A list of ten names means little without "out of
        # how many" and "sized on what"; the board said neither.
        try:
            from ...momentum_rotation import load_universe
            universe_n = len(load_universe()[0])
        except Exception:  # noqa: BLE001
            universe_n = None
        p = self._p()
        capital = float(p.get("capital") or p.get("start_capital") or 0) or None
        return {"strategy": "Momentum rotation (core engine)", "strategy_id": self.strategy_id,
                "universe_n": universe_n, "eligible_n": dec.get("eligible"),
                "capital": capital,
                "today": today,
                "signal_date": dec.get("signal_date"), "order_date": dec.get("order_date"),
                "next_signal_date": dec.get("next_signal_date"),
                "market": ("SPY above its 200-day average: invested" if dec.get("market_ok")
                           else "SPY below its 200-day average: cash"),
                "allocation_pct": round(float(dec.get("allocation") or 0) * 100, 1),
                "per_name_pct": round(float(dec.get("weight_each") or 0) * 100, 2),
                "error": dec.get("error"), "rows": rows, "book": book, "gauges": gauges,
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
                upcoming = decide(nxt[0], self._rot_params())
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
                _pta.push("today-setups", {"universe": self.board_name, "label": "latest",
                                           "uploaded_by": os.uname().nodename,
                                           "artifact": self.board()},
                          base.rstrip("/"), token)   # default retries ~63 s: outlasts an API redeploy
        except Exception as exc:  # noqa: BLE001 — publishing never breaks a run
            _log.warning("could not publish the momentum signal board: %s", str(exc)[:120])


LIVE_OR_DONE = ("FILLED", "PARTIALLY_FILLED", "SUBMITTED", "WORKING", "PENDING_APPROVAL", "APPROVED")


def pick_order_to_show(mine: list[dict]) -> dict | None:
    """The order that describes the trade: a filled or working one beats a
    dead one, however new the dead one is. 25 Nov 2026: the AMD exit was
    SUBMITTED at IBKR and a later duplicate was REJECTED; "newest row" put
    "paper: rejected" on the board beside a sell that was working."""
    if not mine:
        return None
    alive = [o for o in mine if str(o.get("state") or "").upper() in LIVE_OR_DONE]
    pool = alive or mine
    return max(pool, key=lambda o: str(o.get("createdAtUtc") or ""))


def _live_session(closes) -> "str | None":
    """The ONE session an intraday "today" line may describe, as YYYY-MM-DD:
    the New York date once today's market has opened (weekday, ≥ 09:30 ET),
    else the last settled close's date. A 5m series whose last bar belongs to
    an earlier session is a SETTLED row — 23 Nov 2026: MU's 5m store ended on
    Friday and the board printed Friday's −13.9% as "today" beside a live price
    up 18%. Returns None when there is nothing settled to compare against."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() < 5 and (now_et.hour, now_et.minute) >= (9, 30):
        return now_et.date().isoformat()
    if closes is None or not len(closes):
        return None
    return str(closes.index[-1])[:10]

