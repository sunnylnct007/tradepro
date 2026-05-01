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
from .trace import new_trace, AnswerTrace, TRACE_ROOT


def build_server():
    """Construct and return the FastMCP server. Lazily imports so the
    package can be loaded without mcp installed (e.g. for tests)."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("tradepro")

    # ---- TOOLS (LLM-callable functions) -----------------------------------

    @mcp.tool()
    def list_universes() -> str:
        """Available comparator universes (etf_us_core, etf_uk_core, etc.)
        with their freshness. Call this first if you don't know which
        universe a symbol belongs to."""
        return _json(t.list_universes())

    @mcp.tool()
    def get_compare(universe: str) -> str:
        """Full ranked-comparison payload for a universe.
        Includes per-row stats, regime history, sentiment, and
        decision_trace. Each row has a `_source` URI for citation.
        Cite a number as `tradepro://compare/<universe>/rows[<i>]/<field>`.
        """
        return _json(t.get_compare(universe))

    @mcp.tool()
    def get_market_state(symbol: str, lookback_days: int = 365) -> str:
        """Live market state for any ticker on demand — price vs
        SMA200, RSI, drawdown, 52w-high distance, momentum, plus the
        rule-based decision_trace. Use this when the symbol isn't in
        any cached universe.
        """
        return _json(t.get_market_state(symbol, lookback_days))

    @mcp.tool()
    def get_news_with_sentiment(symbol: str, limit: int = 8) -> str:
        """Recent headlines + LLM-scored sentiment per headline.
        7-day rolling summary included. Cite individual headlines as
        `live://news/<symbol>/items[<i>]`."""
        return _json(t.get_news_with_sentiment(symbol, limit))

    @mcp.tool()
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
    def get_health() -> str:
        """API + Mac worker liveness + per-universe cache freshness.
        Always call this first if you suspect data might be stale —
        if cache freshness > 24h, warn the user."""
        return _json(t.get_health())

    @mcp.tool()
    def run_comparison(
        universe: str,
        rank_metric: str = "sharpe",
    ) -> str:
        """Force a fresh comparator run for a universe — fetches
        prices, runs all strategies, scores news, applies the
        decision rules. Slow (10-60s). Prefer `get_compare` first;
        only call this if the user explicitly asks for fresh data."""
        return _json(t.run_comparison(universe, rank_metric))

    @mcp.tool()
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

    # ---- RESOURCES (URIs the client can read directly) --------------------

    @mcp.resource("tradepro://compare/{universe}")
    def compare_resource(universe: str) -> str:
        """Latest cached compare payload for `universe`."""
        return _json(t.get_compare(universe))

    @mcp.resource("tradepro://watchlists")
    def watchlists_resource() -> str:
        """Defined symbol universes (etf_us_core, etf_uk_core, …)."""
        from ..watchlists import WATCHLISTS
        return _json({
            "_source": "tradepro://watchlists",
            "watchlists": {
                name: {"symbols": symbols, "size": len(symbols)}
                for name, symbols in WATCHLISTS.items()
            },
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
