-- 082 — KEEP EVERY DAY'S SIGNALS, NOT JUST TODAY'S.
--
-- Owner, 26 Sep 2026: "the whole purpose of executing it daily is to store all
-- signals and prices so we can easily evaluate diff strategy".
--
-- It was not doing that. today_setups_results has PRIMARY KEY (universe,label)
-- and label defaults to 'latest', so every publish OVERWROTE the previous one.
-- Exactly one row per strategy existed: today's. A month of swing signals from
-- 23 Aug onward was discarded, one morning at a time.
--
-- The cost is not abstract. Live evaluation of the swing sleeve currently rests
-- on EIGHT completed round trips, because the signals it published every day
-- for five weeks are gone. With them we could replay what the strategy actually
-- said against what prices actually did, and separate "the signal was wrong"
-- from "we failed to capture it" — a question nothing can answer today.
--
-- WHY A SECOND TABLE rather than re-keying the first: today_setups_results is
-- read by the boards, the cockpit and the desk check on the (universe,label)
-- key. Re-keying it would make every one of those reads return a history it
-- does not expect. The live surface keeps its shape; the archive is additive.
--
-- ONE ROW PER STRATEGY PER DAY. Re-publishing the same day replaces that day
-- (the last publish of a session is that session's record), but yesterday is
-- never touched. That is the append-only property this needs: today's run can
-- correct today, and can never erase history.

CREATE TABLE IF NOT EXISTS signal_archive (
    strategy        TEXT        NOT NULL,
    as_of_date      DATE        NOT NULL,
    artifact        JSONB       NOT NULL,
    as_of_utc       TIMESTAMPTZ NOT NULL,
    archived_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (strategy, as_of_date)
);

-- The replay access pattern: one strategy, ordered by date.
CREATE INDEX IF NOT EXISTS idx_signal_archive_strategy_date
    ON signal_archive(strategy, as_of_date DESC);

-- "what did every strategy say on date X" — the cross-sectional read.
CREATE INDEX IF NOT EXISTS idx_signal_archive_date
    ON signal_archive(as_of_date DESC);
