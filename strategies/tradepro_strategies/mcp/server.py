"""FastMCP server registering tools, resources, and decomposition prompts.

Run via the `tradepro-mcp` CLI. Default transport is stdio (Claude
Desktop's expectation); HTTP/SSE will be added when the in-app /chat
page lands.
"""
from __future__ import annotations

import json
from typing import Any

from . import tools as t
from . import verify as v
from .session import instrumented, session, session_path
from .trace import new_trace, AnswerTrace, TRACE_ROOT


def build_server():
    """Construct and return the FastMCP server. Lazily imports so the
    package can be loaded without mcp installed (e.g. for tests)."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("tradepro")
    # Touch the session so its file exists from process start, even
    # before the LLM has called anything — gives the operator something
    # to tail while waiting for the first tool call.
    session()

    # ---- TOOLS (LLM-callable functions) -----------------------------------

    @mcp.tool()
    @instrumented("list_universes")
    def list_universes() -> str:
        """Available comparator universes (etf_us_core, etf_uk_core, etc.)
        with their freshness. Call this first if you don't know which
        universe a symbol belongs to."""
        return _json(t.list_universes())

    @mcp.tool()
    @instrumented("get_compare")
    def get_compare(
        universe: str,
        top_n: int = 20,
        fields: str | None = None,
        strip_bloat: bool = True,
    ) -> str:
        """Ranked-comparison payload for a universe — MCP-sized by default.

        Defaults are tuned to fit Claude's tool-result limit on busy
        universes (etf_uk_core with 13 ETFs × 5 strategies = 65 rows
        of full envelopes blows past 1 MB). Override the defaults if
        you specifically need verbose context:

          top_n=0 → return all rows (no truncation; only safe on small
            universes — uk_ftse100_sample produces ~100 rows).
          strip_bloat=False → keep the per-row verbose blocks
            (decision_trace, news, rationale, regimes, fundamentals,
            historical_earnings, swing_score, horizon_classification).
            Together they're ~20 KB per row.
          fields="symbol,bucket,stats,current_action" → return ONLY
            those fields per row. Overrides strip_bloat. Identity +
            citation fields (symbol, strategy, _source) are always
            kept so citations still work.

        For deep per-symbol context, prefer get_market_state /
        get_news_with_sentiment / get_regime_history on the symbols
        you actually want to drill into.

        Cite a number as `tradepro://compare/<universe>/rows[<i>]/<field>`.
        """
        # top_n=0 sentinel → no truncation (None on the tools side).
        effective_top_n: int | None = top_n if top_n > 0 else None
        return _json(t.get_compare(
            universe,
            top_n=effective_top_n,
            fields=fields,
            strip_bloat=strip_bloat,
        ))

    @mcp.tool()
    @instrumented("get_market_state")
    def get_market_state(symbol: str, lookback_days: int = 365) -> str:
        """Live market state for any ticker on demand — price vs
        SMA200, RSI, drawdown, 52w-high distance, momentum, plus the
        rule-based decision_trace. Use this when the symbol isn't in
        any cached universe.
        """
        return _json(t.get_market_state(symbol, lookback_days))

    @mcp.tool()
    @instrumented("get_news_with_sentiment")
    def get_news_with_sentiment(symbol: str, limit: int = 8) -> str:
        """Recent headlines + LLM-scored sentiment per headline.
        7-day rolling summary included. Cite individual headlines as
        `live://news/<symbol>/items[<i>]`."""
        return _json(t.get_news_with_sentiment(symbol, limit))

    @mcp.tool()
    @instrumented("get_regime_history")
    def get_regime_history(
        universe: str,
        symbol: str,
        strategy: str | None = None,
    ) -> str:
        """How a symbol's strategy survived past stress windows
        (GFC, COVID, 2022 rate shock, etc.). If strategy is omitted,
        returns the best-ranked strategy's regime history."""
        return _json(t.get_regime_history(universe, symbol, strategy))

    @mcp.tool()
    @instrumented("get_strategy_leaderboard")
    def get_strategy_leaderboard(
        universe: str,
        symbol: str,
        metric: str = "sharpe",
    ) -> str:
        """Per-symbol strategy leaderboard — the answer to "which
        strategy is doing best on this symbol?". Sorts every strategy
        cell for the symbol by sharpe (default), cagr_pct, or
        max_drawdown_pct and returns the ranked list with action
        labels collapsed to BUY / SELL / HOLD-IN / HOLD-OUT and a
        delta vs the buy-and-hold null model.

        Each row carries `excluded_for_fit` (true when the strategy
        is structurally incompatible with the symbol's factor type —
        e.g. RSI mean-reversion on MTUM) so the LLM can flag "this
        strategy is the top of the list by Sharpe BUT shouldn't vote
        here" instead of recommending a tactically wrong signal.

        Cite individual entries as
        `tradepro://compare/<universe>/leaderboard/<symbol>/strategies[<i>]`.
        """
        return _json(t.get_strategy_leaderboard(universe, symbol, metric))

    @mcp.tool()
    @instrumented("get_instrument_fit")
    def get_instrument_fit(symbol: str) -> str:
        """Instrument-strategy fit classification: which factor type
        is this symbol (momentum / value / quality / low_vol /
        broad_equity / bond / commodity / crypto / single_stock / ...)
        and which TradePro strategies are structurally incompatible
        with that classification.

        Use this BEFORE recommending or rejecting a strategy on a
        specific symbol — the consensus engine already filters
        incompatible votes, but the LLM should be able to explain
        *why* a strategy was suppressed (e.g. "RSI mean-reversion
        was excluded on MTUM because MTUM is a momentum-factor ETF
        and elevated RSI is what it's designed to have").

        Cite as `tradepro://instruments/<symbol>/factor_type`.
        """
        return _json(t.get_instrument_fit(symbol))

    @mcp.tool()
    @instrumented("get_portfolio")
    def get_portfolio() -> str:
        """User's open Trading 212 positions with unrealised P&L per
        row. Each position carries `yahooSymbol` so you can chain
        into get_compare / evaluate_symbols for today's verdict on
        the same ticker. Returns enabled=false when T212 isn't
        configured — surface that to the user instead of guessing."""
        return _json(t.get_portfolio())

    @mcp.tool()
    @instrumented("get_portfolio_status")
    def get_portfolio_status() -> str:
        """Trading 212 connection health: configured / reachable /
        authenticated / mode (demo|live). Use this when get_portfolio
        looks empty to distinguish 'no positions' from 'creds missing'."""
        return _json(t.get_portfolio_status())

    @mcp.tool()
    @instrumented("get_portfolio_signals")
    def get_portfolio_signals(horizon: str = "1y") -> str:
        """Per-position BUY_MORE / HOLD / TRIM recommendations across
        the user's T212 portfolio. Combines current positions (from
        get_portfolio) with today's compare verdict per symbol and
        runs the analyse_holding engine — same logic the dashboard
        and email digest use. Sorted TRIM → BUY_MORE → HOLD so the
        most time-sensitive calls come first.

        horizon ∈ {"6mo", "1y", "3y", "5y"} — picks the threshold
        profile (6mo is most reactive, 5y most patient). Default 1y."""
        return _json(t.get_portfolio_signals(horizon))

    @mcp.tool()
    @instrumented("get_horizon_signals")
    def get_horizon_signals(symbol: str) -> str:
        """Three independent horizon verdicts for one symbol — swing
        (1-8w), long-term (6-18mo), passive (3-5y). Each has its own
        0-8 score, BUY/WATCH/AVOID/N/A signal, reasons list and
        optional entry note. Single-stock symbols return signal
        N/A on the passive horizon (use long-term instead).

        Use when the user asks 'is X a good buy' WITHOUT specifying
        timeframe — the answer differs by horizon and this tool
        surfaces all three in one call. TRADEPRO-SPEC-001 §6.3."""
        return _json(t.get_horizon_signals(symbol))

    @mcp.tool()
    @instrumented("get_hypothetical_return")
    def get_hypothetical_return(
        symbol: str,
        from_date: str,
        to_date: str | None = None,
        quantity: float | None = None,
    ) -> str:
        """"If I'd bought <symbol> on <from_date>, what would my return
        be as of <to_date> (default today)?" Uses split + dividend
        adjusted closes, so a 4-for-1 split mid-hold doesn't break the
        math — return reflects the position you'd actually hold today.

        Returns: buy/sell prices, total return %, annualised return
        (when held >= 30 days), peak/trough between the dates, max
        drawdown, dollar return when `quantity` is given.

        from_date and to_date are YYYY-MM-DD. If the market was closed
        on from_date, the next trading day is used (response says so).
        Cite as `tradepro://hypothetical/<symbol>/<from>/<to>`."""
        return _json(t.get_hypothetical_return(symbol, from_date, to_date, quantity))

    @mcp.tool()
    @instrumented("search_t212_instruments")
    def search_t212_instruments(query: str, limit: int = 10) -> str:
        """Search Trading 212's instruments registry — verifies a
        symbol is tradeable in the user's T212 account. Use before
        recommending a ticker the user might want to actually buy."""
        return _json(t.search_t212_instruments(query, limit))

    @mcp.tool()
    @instrumented("get_health")
    def get_health() -> str:
        """API + Mac worker liveness + per-universe cache freshness.
        Always call this first if you suspect data might be stale —
        if cache freshness > 24h, warn the user."""
        return _json(t.get_health())

    @mcp.tool()
    @instrumented("get_fundamentals")
    def get_fundamentals(symbol: str) -> str:
        """Fundamentals for a single ETF or stock — expense ratio,
        AUM, dividend yield, top-10 holdings, sector weights,
        inception date, summary. Pulls live; cite each field as
        `live://fundamentals/<SYMBOL>/<field>`. Use this to answer
        the long-term-investing questions the BUY/WAIT classifier
        deliberately doesn't ('is this fund expensive?', 'what am I
        actually exposed to?'). Pair with `evaluate_symbols` for the
        full structural-vs-timing picture."""
        return _json(t.get_fundamentals(symbol))

    # ---- Paper trading: orders, fills, snapshots, backtest reports ----

    @mcp.tool()
    @instrumented("get_pending_orders")
    def get_pending_orders() -> str:
        """Paper orders awaiting human approval in manual placement
        mode. Each row has orderId, symbol, side, qty, t212Ticker,
        emit price + state. Pair with approve_paper_order /
        reject_paper_order to act on them."""
        return _json(t.get_pending_orders())

    @mcp.tool()
    @instrumented("approve_paper_order")
    def approve_paper_order(order_id: str) -> str:
        """Approve a Pending paper order — PLACES the market order
        against the configured Trading 212 account (demo or live per
        Trading212__Mode). This is a real action; only call after the
        user confirms. Returns the post-approval order row."""
        return _json(t.approve_paper_order(order_id))

    @mcp.tool()
    @instrumented("reject_paper_order")
    def reject_paper_order(order_id: str, reason: str | None = None) -> str:
        """Reject a Pending paper order. Records the rejection + reason
        on the orders log; no T212 call is made."""
        return _json(t.reject_paper_order(order_id, reason))

    @mcp.tool()
    @instrumented("list_orders")
    def list_orders(symbol: str | None = None, limit: int = 100) -> str:
        """Most-recent orders from the event-sourced orders log.
        Optional symbol filter. Each row carries the strategy, side,
        qty, emit timestamp and the auditable decision_trace."""
        return _json(t.list_orders(symbol, limit))

    @mcp.tool()
    @instrumented("get_order")
    def get_order(order_id: str) -> str:
        """One order + its fills, joined. Use to drill into a specific
        order and trace why the strategy fired."""
        return _json(t.get_order(order_id))

    @mcp.tool()
    @instrumented("get_paper_snapshot")
    def get_paper_snapshot(session_label: str | None = None) -> str:
        """Latest paper-engine snapshot for one session (positions +
        fills + P&L), OR the list of recent sessions when session_label
        is None. Powers the Live tab on the Paper page."""
        return _json(t.get_paper_snapshot(session_label))

    @mcp.tool()
    @instrumented("get_paper_backtest_reports")
    def get_paper_backtest_reports(report_id: str | None = None, limit: int = 50) -> str:
        """When report_id is None, list the most-recent paper-trading
        backtest reports. When given, return the full report (per-
        strategy equity curve, drawdown, fills). The Backtest page
        uses these to compare strategies on the same symbol+range."""
        return _json(t.get_paper_backtest_reports(report_id, limit))

    @mcp.tool()
    @instrumented("list_paper_strategies")
    def list_paper_strategies() -> str:
        """Catalog of registered paper-trading strategies the Mac
        engine has pushed. Empty until tradepro-paper-strategies-push
        runs once on the Mac."""
        return _json(t.list_paper_strategies())

    # ---- Track-record validation: hitrate, scan, evaluate one signal ----

    @mcp.tool()
    @instrumented("get_hitrate")
    def get_hitrate(
        symbol: str,
        strategy: str,
        lookback_years: int = 5,
        horizon_days: int = 20,
    ) -> str:
        """Historical hit-rate for one (symbol, strategy) — out of N
        past signal firings, how many would have made money over the
        next horizon_days. Answers 'does this strategy actually work
        on this symbol?' with backtested evidence."""
        return _json(t.get_hitrate(symbol, strategy, lookback_years, horizon_days))

    @mcp.tool()
    @instrumented("evaluate_signal")
    def evaluate_signal(
        symbol: str,
        strategy: str,
        lookback_years: int = 5,
    ) -> str:
        """Run one strategy against one symbol right now and return
        the decision (BUY/HOLD/SELL + supporting indicators). Use to
        verify cache against a fresh compute."""
        return _json(t.evaluate_signal(symbol, strategy, lookback_years))

    @mcp.tool()
    @instrumented("run_signal_scan")
    def run_signal_scan(
        strategy: str,
        universe: str | None = None,
        symbols_csv: str | None = None,
    ) -> str:
        """Run one strategy across a whole universe or a CSV symbol
        list at once. Use to find current BUY candidates — 'which
        uk-etfs are firing bollinger_bounce today?'"""
        return _json(t.run_signal_scan(strategy, universe, symbols_csv))

    # ---- Event awareness: earnings, analyst recs, upgrades ----

    @mcp.tool()
    @instrumented("get_earnings_calendar")
    def get_earnings_calendar(symbol: str, days: int = 30) -> str:
        """Upcoming earnings for `symbol` over the next `days`
        (max 90). Returns enabled=false when Finnhub key isn't set.
        Use to flag 'MSFT reports in 5 days — position-into-earnings
        volatility risk'."""
        return _json(t.get_earnings_calendar(symbol, days))

    @mcp.tool()
    @instrumented("get_analyst_recommendations")
    def get_analyst_recommendations(symbol: str) -> str:
        """Monthly buy/hold/sell counts from sell-side analysts (last
        ~12 months). Includes momChange — positive means analysts
        are turning bullish month-over-month."""
        return _json(t.get_analyst_recommendations(symbol))

    @mcp.tool()
    @instrumented("get_analyst_upgrades")
    def get_analyst_upgrades(symbol: str, days: int = 30) -> str:
        """Recent analyst upgrade/downgrade events for `symbol` over
        the last `days` (1-180). Includes summary counts + netDelta
        so you can decide 'are analysts piling in or fleeing?'"""
        return _json(t.get_analyst_upgrades(symbol, days))

    # ---- Raw market data: candles ----

    @mcp.tool()
    @instrumented("get_candles")
    def get_candles(
        symbol: str,
        from_date: str,
        to_date: str | None = None,
        interval: str = "1d",
        provider: str | None = None,
    ) -> str:
        """Raw OHLCV candles for `symbol` between two dates. Default
        interval `1d`. Prefer get_hypothetical_return for 'what would
        I have made' questions — this tool is for callers that need
        the bar-by-bar series."""
        return _json(t.get_candles(symbol, from_date, to_date, interval, provider))

    # ---- Settings + control plane ----

    @mcp.tool()
    @instrumented("get_settings")
    def get_settings() -> str:
        """Live application settings — sentiment thresholds, paper-
        trading placementMode (auto|manual). Read before recommending
        changes so you see the current values."""
        return _json(t.get_settings())

    @mcp.tool()
    @instrumented("set_paper_placement_mode")
    def set_paper_placement_mode(mode: str) -> str:
        """Flip paper-trading placement between `auto` (engine places
        orders directly) and `manual` (orders queue as pending). This
        changes how the Mac engine behaves on the NEXT run — confirm
        with the user before flipping."""
        return _json(t.set_paper_placement_mode(mode))

    @mcp.tool()
    @instrumented("list_watchlists")
    def list_watchlists() -> str:
        """Names of every registered watchlist on the server. Use to
        discover symbol groups before drilling into one with
        get_watchlist."""
        return _json(t.list_watchlists())

    @mcp.tool()
    @instrumented("get_watchlist")
    def get_watchlist(name: str) -> str:
        """Members of one watchlist by name. Call list_watchlists
        first to see available names."""
        return _json(t.get_watchlist(name))

    @mcp.tool()
    @instrumented("get_returns")
    def get_returns(symbols: str, periods: str = "1d,5d,30d,90d,ytd") -> str:
        """Multi-period total returns (no backtest, just price math) for
        any basket. Returns dispersion across the basket so the user
        can see who moved how much over 1d / 5d / 30d / 90d / ytd.
        Pair with the `etf_macro_proxies` watchlist for event-impact
        questions ('what's the impact of war/Fed/election?'); a basket
        of correlated S&P-overlap ETFs will give monolithic up-only
        answers and miss the true picture.
        """
        return _json(t.get_returns(symbols, periods))

    @mcp.tool()
    @instrumented("evaluate_symbols")
    def evaluate_symbols(symbols: str, lookback_years: int = 5) -> str:
        """Run every available strategy on any ETF or stock — no
        universe required. `symbols` is a comma-separated ticker list
        (e.g. "VWRP.L,SWDA.L,VUSA.L"). Returns the multi-strategy
        bucket vote (BUY / WAIT / AVOID) per symbol, with per-strategy
        stats + position state, and the now-or-wait market state.
        Slow (~10-15s per symbol). Use this when the user asks about
        a ticker that isn't in any cached universe (call
        `list_universes` first to check)."""
        return _json(t.evaluate_symbols(symbols, lookback_years))

    @mcp.tool()
    @instrumented("run_comparison")
    def run_comparison(
        universe: str,
        rank_metric: str = "sharpe",
        push: bool = False,
    ) -> str:
        """Force a fresh comparator run for a universe — fetches
        prices, runs all strategies, scores news, applies the
        decision rules. Slow (10-60s). Prefer `get_compare` first;
        only call this if the user explicitly asks for fresh data.

        Set `push=true` to also POST the result to /api/ingest/compare
        so the Compare page in the browser sees the refresh next time
        it loads (otherwise the run is ephemeral to this MCP process).
        Push needs credentials in ~/.tradepro/credentials OR
        TRADEPRO_API_URL + TRADEPRO_API_TOKEN env vars."""
        return _json(t.run_comparison(universe, rank_metric, push=push))

    @mcp.tool()
    @instrumented("verify_answer")
    def verify_answer(answer: str, tool_outputs_json: str) -> str:
        """Verify a draft answer against the tool outputs that should
        support it. Returns a per-claim verdict — supported /
        contradicted / unsupported — plus an explicit `should_refuse`
        flag and `refusal_reasons` list. **Hard contract**: if
        `should_refuse` is true, you MUST NOT deliver the draft.
        Either rewrite + re-verify or refuse with the reasons.
        Required before any quantitative answer; unverified numbers
        are worse than no number for financial decisions."""
        try:
            outputs = json.loads(tool_outputs_json)
        except json.JSONDecodeError:
            outputs = tool_outputs_json
        return _json(v.verify_answer(answer, outputs))

    @mcp.tool()
    @instrumented("begin_trace")
    def begin_trace(question: str) -> str:
        """Start a new Q&A trace. Returns a trace_id you should pass
        to record_step / finalize_trace as you work through the
        question. The full chain (decomposition, tool calls, LLM
        calls, draft, verification, outcome) lands at
        `~/.tradepro/traces/<trace_id>.json` and is also exposed at
        the resource URI `tradepro://trace/<trace_id>` so the user
        can audit any answer."""
        tr = new_trace(question)
        tr.save()
        return _json({
            "_source": f"tradepro://trace/{tr.trace_id}",
            "trace_id": tr.trace_id,
            "started_at": tr.started_at,
            "instructions": (
                "Call record_step(trace_id, kind, name, inputs, outputs) "
                "after each meaningful action. Call finalize_trace(trace_id, "
                "outcome, refusal_reasons?) at the end — outcome must be "
                "'delivered' or 'refused'."
            ),
        })

    @mcp.tool()
    @instrumented("record_step")
    def record_step(
        trace_id: str,
        kind: str,
        name: str,
        inputs_json: str = "null",
        outputs_json: str = "null",
        error: str | None = None,
        latency_ms: int | None = None,
    ) -> str:
        """Append one step to a Q&A trace. `kind` should be one of
        decompose | tool_call | llm_call | draft | verify | final."""
        tr = _load_trace(trace_id)
        if tr is None:
            return _json({"ok": False, "error": "trace not found"})
        try:
            inputs = json.loads(inputs_json)
        except json.JSONDecodeError:
            inputs = inputs_json
        try:
            outputs = json.loads(outputs_json)
        except json.JSONDecodeError:
            outputs = outputs_json
        tr.step(kind=kind, name=name, inputs=inputs, outputs=outputs,
                error=error, latency_ms=latency_ms)
        tr.save()
        return _json({"ok": True, "trace_id": trace_id, "step_count": len(tr.steps)})

    @mcp.tool()
    @instrumented("finalize_trace")
    def finalize_trace(
        trace_id: str,
        outcome: str,
        draft_answer: str = "",
        verification_json: str = "null",
        refusal_reasons_json: str = "[]",
    ) -> str:
        """Close a trace with the final outcome. `outcome` must be
        'delivered' (verified, fit to show), 'refused' (verification
        failed and you're returning a refusal), or 'errored'.

        On 'refused' include `refusal_reasons_json` so the user sees
        exactly what claim couldn't be supported."""
        tr = _load_trace(trace_id)
        if tr is None:
            return _json({"ok": False, "error": "trace not found"})
        if outcome not in ("delivered", "refused", "errored"):
            return _json({"ok": False, "error": f"invalid outcome '{outcome}'"})
        if draft_answer:
            tr.draft_answer = draft_answer
        try:
            tr.verification = json.loads(verification_json)
        except json.JSONDecodeError:
            tr.verification = None
        try:
            reasons = json.loads(refusal_reasons_json)
            if isinstance(reasons, list):
                tr.refusal_reasons = [str(r) for r in reasons]
        except json.JSONDecodeError:
            tr.refusal_reasons = [refusal_reasons_json] if refusal_reasons_json else []
        tr.outcome = outcome
        tr.step(kind="final", name=outcome,
                outputs={"refusal_reasons": tr.refusal_reasons})
        path = tr.save()
        return _json({
            "_source": f"tradepro://trace/{trace_id}",
            "ok": True,
            "trace_id": trace_id,
            "outcome": outcome,
            "saved_to": str(path),
        })

    # ---- Alpha engine: COMPASS, sector RS, EPS revision, macro regime, ledger ----

    @mcp.tool()
    @instrumented("get_compass_score")
    def get_compass_score(symbol: str) -> str:
        """Full 6-factor COMPASS score for any ticker on demand.

        Fetches live prices (ensure_cached), yfinance fundamentals,
        sector RS (12w relative strength vs benchmark ETF), and EPS
        revision from stored weekly snapshots. Analyst and sentiment
        factors default to neutral when not pre-scored — to include
        analyst consensus call get_analyst_recommendations first.

        Returns: score (0–100), signal (BUY/WATCH/HOLD/TRIM),
        conviction (HIGH/MEDIUM/LOW), per-factor breakdown, macro_gated
        flag, and the current macro risk mode.

        Takes ~4–8s. Cite as ``live://compass/<SYMBOL>``."""
        return _json(t.get_compass_score(symbol))

    @mcp.tool()
    @instrumented("get_sector_rs")
    def get_sector_rs(symbol: str) -> str:
        """12-week sector relative strength for a symbol vs its
        benchmark sector ETF (e.g. NVDA→SOXX, AAPL→XLK, HSBA.L→EWU).

        Returns rs_score (0–10), rs_12w_pct (raw outperformance),
        sector ETF used, symbol_12w_pct, etf_12w_pct, and a fallback
        flag (True when SPY was used because the sector was unknown).

        rs_score ≥ 7 means the stock is outperforming its sector —
        a tailwind for the COMPASS sector RS factor.

        Cite as ``live://sector_rs/<SYMBOL>``."""
        return _json(t.get_sector_rs(symbol))

    @mcp.tool()
    @instrumented("get_eps_revision")
    def get_eps_revision(symbol: str) -> str:
        """EPS revision direction from locally-stored weekly snapshots.

        Reports whether analyst EPS estimates were raised (up), cut
        (down), or held flat over the last ~90 days. Data comes from
        snapshots written by ``tradepro-refresh --eps-snapshot`` (runs
        every Sunday evening). Returns direction, revision_pct,
        delta_90d, current_estimate, snapshots_count, and as_of date.

        Prerequisite: ≥2 snapshots must exist for the symbol — if
        ``direction`` is ``insufficient_data``, schedule the Sunday
        cron or run the refresh manually once.

        Cite as ``live://eps_revision/<SYMBOL>``."""
        return _json(t.get_eps_revision(symbol))

    @mcp.tool()
    @instrumented("get_macro_regime")
    def get_macro_regime() -> str:
        """Current macro risk mode: GREEN (1), AMBER (2), or RED (3).

        Computed from live VIX, 10-year treasury yield change, and HYG
        high-yield credit spread via yfinance. Day-keyed cache means
        repeat calls within the same calendar day cost nothing.

        Risk-mode implications for COMPASS:
          GREEN (1) — full size (1.0×), BUY signals allowed
          AMBER (2) — reduced size (0.6×), BUY signals dampened to WATCH
          RED (3)   — zero new longs (0.0×), all signals macro_gated

        Cite as ``live://macro_regime``."""
        return _json(t.get_macro_regime())

    @mcp.tool()
    @instrumented("get_signal_ledger_stats")
    def get_signal_ledger_stats(
        source: str | None = None,
        symbol: str | None = None,
        lookback_days: int | None = None,
    ) -> str:
        """Performance stats from the local COMPASS signal ledger.

        The ledger at ``~/.tradepro/signal_ledger.jsonl`` records every
        signal fired by the COMPASS and CATALYST engines. Stats include
        hit_rate_pct, expectancy_pct, total_closed, avg_holding_days,
        and avg_return_pct. Filter by source (COMPASS or CATALYST),
        symbol, and/or lookback_days.

        Also returns open_signals — the count of still-live signals
        whose outcome hasn't been recorded yet. Use to assess track
        record quality before acting on a new signal.

        Cite as ``live://signal_ledger/stats``."""
        return _json(t.get_signal_ledger_stats(source, symbol, lookback_days))

    # ---- RESOURCES (URIs the client can read directly) --------------------

    @mcp.resource("tradepro://compare/{universe}")
    def compare_resource(universe: str) -> str:
        """Latest cached compare payload for `universe`."""
        return _json(t.get_compare(universe))

    @mcp.resource("tradepro://watchlists")
    def watchlists_resource() -> str:
        """Defined symbol universes (etf_us_core, etf_uk_core, …).
        Includes the macro_proxies_by_axis breakdown so callers can
        request "all risk-off proxies" without re-deriving the labels."""
        from ..watchlists import MACRO_PROXIES_BY_AXIS, WATCHLISTS
        return _json({
            "_source": "tradepro://watchlists",
            "watchlists": {
                name: {"symbols": symbols, "size": len(symbols)}
                for name, symbols in WATCHLISTS.items()
            },
            "macro_proxies_by_axis": MACRO_PROXIES_BY_AXIS,
        })

    @mcp.resource("tradepro://regimes")
    def regimes_resource() -> str:
        """The 13 historical stress windows the regime slicer uses."""
        from ..regimes import REGIMES
        return _json({
            "_source": "tradepro://regimes",
            "regimes": [
                {
                    "key": r.key,
                    "name": r.name,
                    "kind": r.kind,
                    "start": r.start.date().isoformat(),
                    "end": r.end.date().isoformat(),
                    "description": r.description,
                }
                for r in REGIMES
            ],
        })

    @mcp.resource("tradepro://health")
    def health_resource() -> str:
        return _json(t.get_health())

    @mcp.resource("tradepro://trace/{trace_id}")
    def trace_resource(trace_id: str) -> str:
        """The full chain of reasoning behind a previous answer —
        decomposition, tool calls, LLM calls, draft, verification
        verdicts, and outcome. Public auditability for every
        decision the chat surface produced."""
        tr = _load_trace(trace_id)
        if tr is None:
            return _json({"_source": f"tradepro://trace/{trace_id}",
                          "ok": False, "error": "trace not found"})
        return _json({"_source": f"tradepro://trace/{trace_id}",
                      "ok": True, "trace": tr.to_dict()})

    @mcp.resource("tradepro://session/current")
    def session_resource() -> str:
        """Live process-scoped trace — every tool/resource invocation
        in this MCP server process, regardless of whether the LLM
        called begin_trace. Useful for post-hoc audit of any Q&A,
        even casual chat turns."""
        s = session()
        try:
            data = json.loads(s.path.read_text()) if s.path.exists() else {
                "kind": "session_trace",
                "session_id": s.session_id,
                "started_at": s.started_at,
                "step_count": 0,
                "steps": [],
            }
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"session_id": s.session_id, "step_count": 0, "steps": []}
        return _json({
            "_source": "tradepro://session/current",
            "path": str(s.path),
            "session": data,
        })

    @mcp.resource("tradepro://traces")
    def traces_index_resource() -> str:
        """List of recent answer traces (newest first). Each entry is
        clickable through to `tradepro://trace/<trace_id>` for the
        full chain."""
        if not TRACE_ROOT.exists():
            return _json({"_source": "tradepro://traces", "traces": []})
        files = sorted(
            TRACE_ROOT.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:50]
        items = []
        for f in files:
            try:
                d = json.loads(f.read_text())
                items.append({
                    "trace_id": d.get("trace_id"),
                    "question": d.get("question", "")[:120],
                    "outcome": d.get("outcome"),
                    "started_at": d.get("started_at"),
                    "ended_at": d.get("ended_at"),
                    "step_count": len(d.get("steps") or []),
                    "uri": f"tradepro://trace/{d.get('trace_id')}",
                })
            except Exception:  # noqa: BLE001
                continue
        return _json({"_source": "tradepro://traces", "traces": items})

    # ---- PROMPTS (decomposition templates) --------------------------------

    @mcp.prompt()
    def analyse_etf(symbol: str) -> str:
        """Should I invest in `symbol`? Decomposes into sub-questions,
        forces tool calls before answering, requires verification."""
        return _DECOMPOSE_TEMPLATE.format(
            question=f"Should I invest in {symbol} today?",
            symbol=symbol,
            sub_questions=(
                f"  1. Which universe is {symbol} in? (call list_universes)\n"
                f"  2. What's its current verdict + why? (call get_compare,"
                f" then read rows where symbol == '{symbol}')\n"
                f"  3. How has it survived past stress?"
                f" (call get_regime_history)\n"
                f"  4. What's recent news + sentiment?"
                f" (call get_news_with_sentiment)\n"
                f"  5. Is the data fresh? (call get_health)"
            ),
        )

    @mcp.prompt()
    def compare_etfs(symbols: str) -> str:
        """Compare two or more ETFs. `symbols` is a comma-separated list."""
        return _DECOMPOSE_TEMPLATE.format(
            question=f"Compare these ETFs: {symbols}.",
            symbol=symbols,
            sub_questions=(
                "  1. Find the universe(s) containing the symbols.\n"
                "  2. For each, get the current verdict + best-strategy stats.\n"
                "  3. For each, get the regime history.\n"
                "  4. Tabulate the differences side-by-side; cite every cell."
            ),
        )

    @mcp.prompt()
    def analyse_event(event: str) -> str:
        """Decompose a 'what's the market impact of <event>?' question.
        Forces dispersion-first analysis using the etf_macro_proxies
        watchlist so the answer surfaces who moved up vs down — not a
        monolithic "everything's up" from sampling correlated ETFs."""
        return _DISPERSION_TEMPLATE.format(event=event)

    @mcp.prompt()
    def should_i_buy_today(universe: str = "etf_us_core") -> str:
        """What's worth buying *today*? Reads the verdict bucket."""
        return _DECOMPOSE_TEMPLATE.format(
            question=f"What should I buy today from `{universe}`?",
            symbol=universe,
            sub_questions=(
                f"  1. Get the cache freshness (call get_health).\n"
                f"  2. Pull the universe (call get_compare with universe="
                f"'{universe}').\n"
                f"  3. List rows where the verdict bucket is BUY — cite each.\n"
                f"  4. For the top 1-3, summarise the supporting signals."
            ),
        )

    return mcp


_DISPERSION_TEMPLATE = """You are a careful, evidence-grounded
financial-research assistant. The user asked about the market impact
of: {event}.

**Why this prompt exists:** the lazy answer is to pull two or three
broad-market ETFs (SPY, VWRP, SWDA), see they're all up, and conclude
"the event hasn't moved markets". That's wrong. Those funds share
~70% of their constituents — they will always agree. The real
picture lives in DISPERSION across uncorrelated proxies.

**Process — non-negotiable:**

Step 1. Read the canonical macro basket from the watchlists resource
so this prompt never drifts from the codebase definition:
    Read resource `tradepro://watchlists`. Use the `etf_macro_proxies`
    list as your symbols, and the `macro_proxies_by_axis` map to
    label each result with its axis (risk_on_equity, risk_off_bonds,
    risk_off_metal, commodity, sector_event, currency, volatility).
Then call:
    get_returns(symbols=<comma-joined etf_macro_proxies>,
                periods="1d,5d,30d,90d,ytd")
get_returns already includes a `macro_axis` field on each row — use
it verbatim; do NOT re-classify symbols from memory.

Step 2. From that table, build the dispersion picture:
  - Identify the 3 biggest gainers and 3 biggest losers over the
    relevant horizon (5d for an acute event, 30d for a slow burn).
  - For each, name the axis (`macro_axis` from get_returns row) and
    quote the percentage with its `_source` URI:
    "GLD (risk_off_metal) +6.2% [live://returns/GLD/return_30d_pct]".
  - Flag any axis pair that moved oppositely — risk_off_metal up +
    risk_on_equity down is classic risk-off rotation; sector_event
    (XLE/ITA) up + risk_on_equity (EEM) down is geopolitical stress.

Step 3. Pull recent news + sentiment for the proxies that moved most:
  get_news_with_sentiment(symbol=<biggest mover>) for the top 2-3.

Step 4. Draft the answer. Lead with the dispersion table — concrete
numbers, every one cited. Only after the data is on the page may you
add interpretation (why oil is up, why bonds rallied, etc.). The
news provides the *narrative*; the returns provide the *evidence*.

Step 5. Verify. Call `verify_answer(answer=<draft>, ...)` against the
returns + news outputs. If `should_refuse=true`, rewrite to remove
the unsupported claim.

**Hard rules:**
- NEVER answer an event-impact question by sampling only broad-market
  ETFs (SPY/VWRP/SWDA/VUSA/QQQ alone). They're correlated; they will
  lie by omission.
- NEVER lead with the narrative. Lead with the data; let the user
  read it before you frame it.
- Every percentage MUST cite a `_source` URI from get_returns. Numbers
  without sources are grounds for refusal.
"""


_DECOMPOSE_TEMPLATE = """You are a careful, evidence-grounded financial-
research assistant. The user asked: {question}

**Process — non-negotiable, fail-closed:**

Step 0. Call `begin_trace(question="{question}")` first. Save the
returned `trace_id`; you'll attach every subsequent step to it.

Step 1. Decompose the question into atomic sub-questions and record
the decomposition via `record_step(trace_id, "decompose", "plan",
inputs_json=..., outputs_json=...)`:
{sub_questions}

Step 2. Call the relevant tools to gather the facts. After each tool
call, record it: `record_step(trace_id, "tool_call", <tool_name>,
inputs_json, outputs_json)`. Do NOT answer from memory — every
quantitative claim must come from a tool response.

Step 3. Draft the answer. Each number or rule cited carries the
`_source` path returned by the tool (e.g.
"Sharpe 0.94 [tradepro://compare/etf_us_core/rows[0]/stats/sharpe]").

Step 4. Call `verify_answer(answer=<draft>, tool_outputs_json=...)`.
Inspect the response:
  - If `should_refuse` is **false** → call
    `finalize_trace(trace_id, outcome="delivered",
                     draft_answer=<draft>,
                     verification_json=<verify response>)` and return
    the answer.
  - If `should_refuse` is **true** → you have ONE chance to rewrite
    by removing/correcting the unsupported claims, then re-verify.
    If still failing, **REFUSE**: call `finalize_trace(trace_id,
    outcome="refused", refusal_reasons_json=<from verify>)` and
    return ONLY: "I cannot answer this with confidence because:
    <list the refusal_reasons>." Never deliver an unverified
    quantitative answer. **Hallucinated numbers are worse than no
    answer.**

**Hard rules — never bend:**
- The actual BUY / SELL / HOLD verdict comes from the rule engine. It
  is the `bucket` field on each row of the compare payload. You may
  explain *why* the engine said BUY, never override it.
- If data is stale (cache > 24h via `get_health`) or the worker is
  down, lead the answer with that caveat — and consider it a partial
  refusal until the user confirms they're OK with stale data.
- Every number must cite a `_source`. A number without a citation
  is grounds for refusal.
- If you're not certain, refuse. **Doubt → refuse.** This is a
  financial-decision tool — accuracy outranks helpfulness.
"""


def _load_trace(trace_id: str) -> "AnswerTrace | None":
    path = TRACE_ROOT / f"{trace_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    tr = AnswerTrace(
        trace_id=data["trace_id"],
        question=data["question"],
        started_at=data["started_at"],
    )
    tr.decomposition = data.get("decomposition")
    tr.draft_answer = data.get("draft_answer")
    tr.verification = data.get("verification")
    tr.outcome = data.get("outcome")
    tr.refusal_reasons = list(data.get("refusal_reasons") or [])
    from .trace import TraceStep
    for s in data.get("steps") or []:
        tr.steps.append(TraceStep(**s))
    return tr


def _json(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)
