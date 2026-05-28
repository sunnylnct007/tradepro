-- 022_llm_evaluations.sql
--
-- LLM decision audit log — stores every LLM evaluation that touches an
-- order so the operator can answer "10 orders came in, 5 got approved,
-- 5 got rejected — on what basis?" without going blind.
--
-- An evaluation is recorded BEFORE the OMS decision is committed so the
-- two are time-correlated. Even when the LLM is wired as an advisor
-- (not yet an approver), every call lands in this table.
--
-- This is the LLM mirror of risk_events — risk_events captures C#
-- RiskGate refusals; llm_evaluations captures LLM model rationale.
-- Together they form the full audit chain for any order decision.

CREATE TABLE IF NOT EXISTS llm_evaluations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- What the LLM was asked about. order_id is nullable because some
    -- evaluations are pre-order (signal-only) — those don't yet have an
    -- OMS row. When the evaluation drives a downstream order, the
    -- enqueuer back-fills order_id.
    order_id        UUID REFERENCES oms_orders(id) ON DELETE SET NULL,
    strategy_id     TEXT,
    symbol          TEXT,
    side            TEXT,
    qty             NUMERIC,
    broker          TEXT,

    -- Provenance — which LLM gave this verdict. llm_url is the endpoint
    -- (ollama / openai / local-wrapper) — same shape settings as in
    -- app_settings_kv. model_name is the actual model string (e.g.
    -- "llama3.1:8b-instruct", "finbert-prosus", "gpt-4o").
    purpose         TEXT NOT NULL,          -- approve_order / sentiment_score / pre_trade_review …
    llm_url         TEXT NOT NULL,
    llm_model       TEXT NOT NULL,
    source_tag      TEXT,                   -- mirrors sentiment_scores.source for A/B
    latency_ms      INTEGER,

    -- The conversation. prompt + response are the verbatim payloads so
    -- the operator can replay a decision into a different model or
    -- inspect for prompt-injection / hallucination. Truncated to 16KB
    -- each to bound table growth (typical prompt < 4KB).
    prompt          TEXT NOT NULL,
    response_raw    TEXT NOT NULL,

    -- The parsed verdict. decision is one of:
    --   APPROVE    — LLM endorsed the order
    --   REJECT     — LLM blocked the order
    --   ADVISE     — score-only (sentiment); no go/no-go opinion
    --   ERROR      — LLM call failed; reason in response_raw
    -- confidence is 0..1 when the LLM reports one, NULL otherwise.
    decision        TEXT NOT NULL,
    confidence      NUMERIC,
    reasoning       TEXT,                   -- one-paragraph human-readable why
    detail_json     JSONB                   -- structured signals: bull/bear catalysts, factor scores, etc.
);

-- Lookups we'll do most often:
--   1. "All evaluations for this order" — order detail page audit trail
CREATE INDEX IF NOT EXISTS idx_llm_evals_order
    ON llm_evaluations (order_id, occurred_at_utc DESC)
    WHERE order_id IS NOT NULL;

--   2. "Daily approve/reject histogram" — /risk dashboard
CREATE INDEX IF NOT EXISTS idx_llm_evals_recent
    ON llm_evaluations (occurred_at_utc DESC, decision);

--   3. "All evaluations from model X" — A/B comparison
CREATE INDEX IF NOT EXISTS idx_llm_evals_model
    ON llm_evaluations (llm_model, occurred_at_utc DESC);
