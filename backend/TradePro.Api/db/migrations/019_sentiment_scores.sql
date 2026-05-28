-- 019_sentiment_scores.sql
--
-- Per-symbol sentiment snapshot, populated by a local-LLM service
-- on the Mac (user runs a finance-tuned LLM locally — Ollama-style
-- HTTP endpoint at localhost:11434, model picks up news from
-- news_sentiment.py and classifies positive/negative/neutral).
--
-- Two consumers:
--   1. RiskGate (pre-trade) — when a BUY intent comes through, look
--      up the latest sentiment for the symbol. If negative below
--      threshold, veto the BUY with reason "negative_sentiment".
--      Never blocks SELLs (those are defensive — we want OUT when
--      sentiment turns).
--   2. RiskMonitorService (continuous) — when sentiment on a HELD
--      position drops past threshold, emit a position_sentiment_alert
--      risk_event (auto-exit wiring follows).
--
-- Latest = MAX(scored_at_utc) per symbol. Historical lookback via
-- the same table (filter by symbol + date range).

CREATE TABLE IF NOT EXISTS sentiment_scores (
    symbol            TEXT NOT NULL,
    source            TEXT NOT NULL,         -- 'finbert-local' | 'gpt4-cloud' | etc.
    -- Score in [-1, 1] — caller's convention. -1 = strongly negative,
    -- +1 = strongly positive. The classification column is the
    -- LLM's discrete label; score is the underlying continuous signal.
    score             DOUBLE PRECISION NOT NULL,
    classification    TEXT NOT NULL CHECK (classification IN
        ('strongly_negative', 'negative', 'neutral', 'positive', 'strongly_positive')),
    n_articles        INT NOT NULL DEFAULT 0,
    -- The LLM's summary line — short, audit-friendly. NULL if the
    -- caller didn't synthesise one.
    rationale         TEXT,
    -- Source articles considered, as JSONB array of {title, url, score}.
    -- Optional; NULL when caller only stored the aggregate.
    detail            JSONB,
    scored_at_utc     TIMESTAMPTZ NOT NULL,  -- when the LLM scored it
    uploaded_at_utc   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, source, scored_at_utc)
);

CREATE INDEX IF NOT EXISTS idx_sentiment_scores_latest
    ON sentiment_scores(symbol, scored_at_utc DESC);

-- Settings for the sentiment gate. Defaults conservative: block
-- BUYs only when classification is 'strongly_negative' AND score
-- below -0.5. Tunable once we see real signal-to-noise from the
-- local LLM (user will calibrate via paper trading).
INSERT INTO app_settings_kv
    (key, value, value_type, label, description, category, min_value, max_value)
VALUES
    ('risk_sentiment_buy_veto_score',
     '-0.5'::jsonb, 'number',
     'Sentiment score below which BUYs are vetoed',
     'When the latest sentiment score for a symbol is below this '
     || 'threshold, RiskGate refuses new BUYs on that symbol with '
     || 'reason "negative_sentiment". Range [-1, 0]; 0 disables the '
     || 'veto. Default -0.5.',
     'Risk', -1.0, 0.0),
    ('risk_sentiment_position_alert_score',
     '-0.7'::jsonb, 'number',
     'Sentiment score on held positions that triggers an alert',
     'When sentiment on a HELD position drops below this score, '
     || 'RiskMonitorService logs a position_sentiment_alert risk_event '
     || 'so the operator can review. More aggressive than the BUY '
     || 'veto — only fires on clearly bad news. Default -0.7.',
     'Risk', -1.0, 0.0),
    ('risk_sentiment_max_age_minutes',
     '60'::jsonb, 'number',
     'Max age of sentiment data to trust (minutes)',
     'Sentiment older than this is ignored — the gate behaves as if '
     || 'no sentiment exists. Stops a 6-hour-old "negative" tag from '
     || 'blocking entries when sentiment may have flipped. Default 60.',
     'Risk', 5, 1440)
ON CONFLICT (key) DO NOTHING;
