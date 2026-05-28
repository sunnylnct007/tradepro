-- 006_events_notify.sql
-- Postgres LISTEN/NOTIFY wiring for the events table.
--
-- Phase 7 of VISION.md (real-time event stream). The .NET API opens
-- a long-lived LISTEN connection per SSE subscriber and forwards each
-- notification to the client. Payload is just the new seq number —
-- NOTIFY's 8000-byte limit makes shipping the full JSONB unsafe, and
-- the subscriber can fetch the row by seq cheaply.
--
-- The trigger is AFTER INSERT so failed inserts never produce phantom
-- notifications. A re-run that finds the trigger already present
-- replaces it (CREATE OR REPLACE FUNCTION + DROP/CREATE TRIGGER).

CREATE OR REPLACE FUNCTION events_notify() RETURNS trigger AS $$
BEGIN
    -- pg_notify is the function form of NOTIFY — works inside a
    -- function body where the static NOTIFY statement would not.
    -- Channel name is intentionally short; the payload is the seq.
    PERFORM pg_notify('tradepro_events', NEW.seq::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_notify_trigger ON events;
CREATE TRIGGER events_notify_trigger
AFTER INSERT ON events
FOR EACH ROW EXECUTE FUNCTION events_notify();

COMMENT ON FUNCTION events_notify() IS
    'Emits NOTIFY tradepro_events <seq> on every events insert. Powers the SSE stream — subscribers LISTEN and re-fetch by seq.';
