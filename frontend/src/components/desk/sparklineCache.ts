/**
 * sparklineCache — session-scoped, concurrency-limited loader of short daily
 * close-price series for /desk position-row sparklines.
 *
 * Why this exists:
 *   - The Portfolio table can show dozens of position rows. Firing one
 *     `api.candles()` request per row in parallel would flood the marketdata
 *     endpoint (and the provider behind it). So every fetch goes through a
 *     small fixed-size pool (POOL_SIZE) — at most that many candle requests are
 *     ever in flight at once; the rest queue.
 *   - Results are memoised per symbol for the lifetime of the page (a module
 *     level Map). A symbol that's already been fetched (or is in flight)
 *     returns the same promise instead of re-hitting the network on the table's
 *     60s refresh tick.
 *
 * Honesty rule (user: never fabricate a series): on empty/failed candles we
 * resolve to `null`, never a synthetic line. Callers pass that straight to
 * <Sparkline/>, which renders the "—" placeholder.
 */
import { api } from "../../api/client";

const POOL_SIZE = 5;          // max concurrent candle requests
const LOOKBACK_DAYS = 45;     // ~30 daily bars after weekends/holidays

type Series = number[] | null;

const cache = new Map<string, Promise<Series>>();

let active = 0;
const queue: (() => void)[] = [];

/** Run `task` once a pool slot is free; release the slot when it settles. */
function schedule<T>(task: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const run = () => {
      active += 1;
      task()
        .then(resolve, reject)
        .finally(() => {
          active -= 1;
          const next = queue.shift();
          if (next) next();
        });
    };
    if (active < POOL_SIZE) run();
    else queue.push(run);
  });
}

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function isoToday(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Fetch ~30 daily closes for `symbol`, concurrency-limited + session-cached.
 * Resolves to the close array (>=2 points) or `null` when no honest series is
 * available. Never rejects — failures degrade to `null`.
 */
// ── Self-diagnosis (13 Aug 2026 — the Trend column read "—" for SIX DAYS
// while every backend layer checked out; the silent catch below hid the
// browser-side reason the whole time). Every failure is now recorded WITH
// its reason and surfaced: sparklineStats() feeds the table footers, and
// each failure console.warns so devtools shows the culprit immediately.
export type SparkStats = {
  requested: number;
  loaded: number;
  failed: number;
  lastError: string | null;
};
const stats: SparkStats = { requested: 0, loaded: 0, failed: 0, lastError: null };
export function sparklineStats(): SparkStats {
  return { ...stats };
}

export function loadSparkline(symbol: string): Promise<Series> {
  const key = symbol.trim();
  if (!key) return Promise.resolve(null);

  const existing = cache.get(key);
  if (existing) return existing;

  stats.requested += 1;
  const p = schedule(async (): Promise<Series> => {
    try {
      const res = await api.candles({
        symbol: key,
        interval: "1d",
        from: isoDaysAgo(LOOKBACK_DAYS),
        to: isoToday(),
      });
      const closes = (res.candles ?? [])
        .map((c) => c.close)
        .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
      if (closes.length >= 2) {
        stats.loaded += 1;
        return closes;
      }
      stats.failed += 1;
      stats.lastError = `${key}: candles returned ${res.candles?.length ?? 0} rows, ${closes.length} numeric closes`;
      console.warn("[sparkline]", stats.lastError, res);
      return null;
    } catch (e) {
      stats.failed += 1;
      stats.lastError = `${key}: ${e instanceof Error ? e.message : String(e)}`;
      console.warn("[sparkline]", stats.lastError);
      return null; // degrade gracefully — no fabricated series
    }
  }).catch((e) => {
    stats.failed += 1;
    stats.lastError = `${key}: ${e instanceof Error ? e.message : String(e)}`;
    console.warn("[sparkline]", stats.lastError);
    return null;
  });

  cache.set(key, p);
  return p;
}
