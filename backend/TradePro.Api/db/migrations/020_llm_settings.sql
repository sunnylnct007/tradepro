-- 020_llm_settings.sql
--
-- LLM connection + behaviour settings, persisted in app_settings_kv
-- so the operator can tune the local-LLM endpoint without redeploying.
-- Sensitive items (api keys for hosted LLMs, if ever) stay in AWS
-- Secrets Manager; everything tunable lives here.

INSERT INTO app_settings_kv
    (key, value, value_type, label, description, category, min_value, max_value)
VALUES
    ('llm_url',
     '"http://localhost:11434/api/generate"'::jsonb, 'string',
     'Local LLM endpoint URL',
     'HTTP endpoint the sentiment-score CLI posts to. Ollama by '
     || 'default. Update when switching to a hosted LLM. Must accept '
     || 'the Ollama /api/generate request shape.',
     'LLM', NULL, NULL),
    ('llm_model',
     '"llama3.1:8b-instruct"'::jsonb, 'string',
     'Local LLM model name',
     'Model identifier passed to the LLM endpoint. Default '
     || 'llama3.1:8b-instruct (general capable). Pull via '
     || '`ollama pull <model>`. For true FinBERT accuracy, run a '
     || 'separate transformers wrapper and point llm_url at it.',
     'LLM', NULL, NULL),
    ('llm_source_tag',
     '"local-llm"'::jsonb, 'string',
     'Source tag on sentiment_scores rows',
     'Stored in sentiment_scores.source so the operator can audit '
     || 'which LLM generated a score (and run A/B between models by '
     || 'changing this tag).',
     'LLM', NULL, NULL)
ON CONFLICT (key) DO NOTHING;

-- Default broker for /scan + auto-execute when the trade plan needs
-- a routing target. Operator switches between T212_DEMO and IG_DEMO
-- by editing this setting (no redeploy).
INSERT INTO app_settings_kv
    (key, value, value_type, label, description, category, min_value, max_value)
VALUES
    ('default_broker',
     '"T212_DEMO"'::jsonb, 'string',
     'Default broker for auto-execute',
     'Broker label assigned to orders produced by tradepro-live-portfolio '
     || '--auto-execute and the /scan flow. T212_DEMO is the long-running '
     || 'default; switch to IG_DEMO once the IG creds are loaded and '
     || 'the IG broker passes a manual test order. Values: T212_DEMO / '
     || 'T212_LIVE / IG_DEMO / IG_LIVE / PAPER.',
     'Trading', NULL, NULL)
ON CONFLICT (key) DO NOTHING;
