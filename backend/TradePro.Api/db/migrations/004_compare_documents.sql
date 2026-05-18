-- 004_compare_documents.sql
-- The two highest-volume stores: compare cache (one big JSON per
-- universe, refreshed 5x/day) and documents (uploaded PDFs/HTML
-- plus their extracted text). Both stay JSONB because the payload
-- shape is owned by the Python side.

-- ── compare_cache ────────────────────────────────────────────────
-- One row per universe. The Mac's `tradepro-refresh` pushes a fresh
-- compare result here on every fire; we replace the row in place.
-- summary is a denormalised projection of payload for the
-- `ListUniverses()` query so we don't pay JSONB-parse cost to render
-- the universe selector pills.
CREATE TABLE IF NOT EXISTS compare_cache (
    universe         TEXT        PRIMARY KEY,
    payload          JSONB       NOT NULL,
    summary          JSONB       NOT NULL,
    row_count        INT         NOT NULL DEFAULT 0,
    received_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS compare_cache_updated
    ON compare_cache (updated_at DESC);

COMMENT ON TABLE compare_cache IS
    'Latest compare result per universe. Replaced in place on every Mac refresh. Summary column lets ListUniverses() skip the big payload.';

-- ── documents ────────────────────────────────────────────────────
-- Documents uploaded for symbol context (10-Ks, earnings transcripts,
-- analyst notes). Metadata + JSONB envelope here; extracted text in
-- a separate document_text table so the LIST query doesn't transfer
-- megabytes per row when only the metadata is needed.
--
-- linked_symbols is TEXT[] (Postgres array) so we can query "all docs
-- linked to AAPL" with a GIN index in O(log n).
CREATE TABLE IF NOT EXISTS documents (
    doc_id           TEXT        PRIMARY KEY,
    title            TEXT        NOT NULL,
    file_kind        TEXT        NOT NULL,
    extractor        TEXT        NOT NULL,
    char_count       INT         NOT NULL DEFAULT 0,
    page_count       INT,
    linked_symbols   TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
    payload          JSONB       NOT NULL,
    source_url       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS documents_linked_symbols
    ON documents USING GIN (linked_symbols);

CREATE INDEX IF NOT EXISTS documents_created
    ON documents (created_at DESC);

CREATE TABLE IF NOT EXISTS document_text (
    doc_id  TEXT PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    text    TEXT NOT NULL
);

COMMENT ON TABLE documents IS
    'Uploaded documents (10-Ks, transcripts, notes) with metadata and the original envelope. Text lives in document_text to keep List() queries small.';
COMMENT ON COLUMN documents.linked_symbols IS
    'Symbols this document is associated with. Indexed with GIN so "docs for AAPL" is fast.';
