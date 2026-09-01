# One screen, one universe, one email

**Owner, 1 Sep 2026:** *"keep on telling u we want a coherant and trustworthy data
and not scattered data"* … *"as user i dont have to think many screens"*.

This has been asked repeatedly — 15 Aug (*"we shouldn't hit issues where we don't
know if data is coming from cache, yahoo or ibkr"*), 30 Aug (*"i still do not want
2 diff path of data acesss … one api shd be there for core ddata acccess"*), and
again today. It has been answered with individual fixes each time. This is the
plan instead.

---

## What is actually there today (audited, not assumed)

**Seven candidate producers. Six universes. Four email senders. Two endpoints.**

| Producer | Universe | Publishes to | Emails |
|---|---|---|---|
| `today_setups` | large_50, high_beta | `today-setups/{universe}` | no |
| `swing_candidates` | 244 committed | `today-setups/swing` | no |
| `momentum_candidates` | 244 committed | `today-setups/momentum` | no |
| `post_earnings_puts` | 244 → recent reporters | `today-setups/post_earnings_puts` | no |
| `options_screen` (wheel) | 82 curated | **`/api/options/candidates`** | **yes** |
| `screener/daily_run` | **30 hardcoded** | **nowhere** | **yes (wheel + swing)** |
| `straddle_scan` / index strangle | 8 indices | own path | **yes** |

### The three concrete symptoms

1. **Two things called "wheel" disagreed in the same afternoon.** 1 Sep:
   `options_screen` said **21 eligible**; `screener/daily_run` emailed **0
   candidates**. Different universe (82 vs 30), different logic (14 gates vs a
   14-point score), different data (IBKR chain vs snapshot fields). Both mailed
   the owner.

2. **A screen said "nothing qualifies" when it meant "I could not see".** The
   `daily_run` wheel requested nine snapshot fields and received two; the other
   seven defaulted to `0.0`, and the one that DID arrive (`"11.950%"`) failed a
   bare `double.TryParse` and also became `0.0`. Every name then scored 4/14
   against a minimum of 5. Fixed in `7e5998e`, but only the symptom.

3. **The desk asks the owner to hold six screens in their head** — Swing,
   Momentum, Puts, Wheel, Strangle, Today's Setups — each with its own universe,
   its own freshness and its own idea of what a candidate is.

### What is already right, and should be built on

`today_setups`, `swing_candidates`, `momentum_candidates` and `post_earnings_puts`
**already publish to one endpoint** (`/api/ingest/today-setups`) keyed by
universe. Four of seven producers are halfway there. `options_screen` is the
outlier that went its own way, and `screener/daily_run` never joined at all.

---

## The target

**One universe.** The 244-name committed universe is already the definition of
what we trade. `large_50`, `high_beta` and the hardcoded 30 become **tags on it**,
not separate lists.

**One candidate record.** Every screen emits the same shape:

```
symbol · strategy · as_of · entry · stop/exit · size basis
evidence  (the strategy's own measured record, not a backtest)
provenance (per input: IBKR / cache / vendor / fallback / missing)
gates     (what was checked, what passed, what blocked)
```

**One screen.** A single Candidates table, filtered by strategy — the way the
wheel board already filters with `hide blocked`. Not six tabs.

**One email.** Today's candidates across all strategies, each beside its own
sleeve's real fill record.

**One freshness rule.** Nothing renders a prior-close candidate during market
hours without saying so, on the row.

---

## The work, in order of value

Each phase stands alone and ships independently. Cut from the bottom.

### Phase 1 — one endpoint, one wheel  ·  ~half a day  ·  highest value

* `options_screen` publishes to `/api/ingest/today-setups` under
  `universe="wheel"`, exactly like the other four. Its
  `/api/options/candidates` endpoint stays as a read alias so nothing breaks.
* `screener/daily_run` **stops scoring its own wheel**. Its email sources the
  canonical artifact. The 14-point score disappears from the email — it is a
  second definition of eligibility and it is the one running on dead fields.
* Its rich presentation (charts, Claude analysis, support/resistance) is KEPT
  and applied to the canonical names.

**Result:** one definition of a wheel candidate. One wheel email. The 21-vs-0
contradiction becomes impossible rather than merely fixed.

**Risk:** the email template shows `Score X/14`, which will no longer exist. It
should show the gate verdict (`14 checked · 14 passed`) — a real quantity from
`decision_trace`, already on every row.

### Phase 2 — one universe with tags  ·  ~half a day

* `build_universe` emits `tags: ["large_50", "high_beta", "wheel", …]` per symbol.
* `today_setups`, `options_screen` and `daily_run` select by tag instead of
  holding their own lists. Delete the hardcoded 30 and the curated 82.
* `harvest_symbols` stays the union it already is.

**Result:** adding a name is one edit. No screen can silently run on a different
universe than the others — the failure that made "0 of 30" and "21 of 82" look
like a strategy disagreement.

### Phase 3 — one candidate record  ·  ~1 day

* A `Candidate` dataclass in `tradepro_strategies/candidates.py`, emitted by
  every producer, validated on ingest.
* Carries provenance (already exists on wheel rows via `row_provenance`) and the
  gate trace (already exists via `decision_trace`) for ALL strategies, not just
  the wheel.
* The API rejects a candidate with no provenance rather than storing it.

**Result:** "is this IBKR, cache or Yahoo?" is answerable on every row of every
strategy, not just the wheel.

### Phase 4 — one screen  ·  ~1 day

* A single **Candidates** tab: one table, strategy filter, sortable columns,
  reason codes, prose behind a click — the pattern `WheelBoardTable` already
  proves.
* Each row shows the strategy's own measured record beside it, the way Today's
  Setups already shows `44%` / `24%` win rates. A candidate from a losing sleeve
  must not look like one from a winning sleeve.
* Swing / Momentum / Puts / Wheel become filters, not tabs.

**Result:** the owner reads one table.

### Phase 5 — one email, one freshness rule  ·  ~half a day

* A single daily digest across strategies. Retire the separate wheel and swing
  senders.
* A candidate computed on a prior close renders with an explicit stale marker
  during market hours — partially built already in Today's Setups, applied
  everywhere.

---

## What this does NOT fix, and should not pretend to

* **The wheel's `DO NOT FUND` verdict stands.** Coherence is about the plumbing.
  The v3 backtest result is untouched by any of this.
* **IV-Rank needs ~60 days of its own dataset** and has ~18. It will read `n/a`
  until it matures whatever we do.
* **Momentum's evidence has never been reviewed.** Making its candidates render
  in the same table as everything else does not validate them — and the sleeve
  record beside each row is what will make that visible.

---

## Recommended cut

If only one phase gets built: **Phase 1**. It removes the contradiction the owner
actually hit today, ends the duplicate wheel definition, and leaves one wheel
email.

If two: **Phase 1 + Phase 4** — one definition, and one screen to read it on.

Phases 2, 3 and 5 are the durable version and are worth doing, but nothing breaks
if they wait.
