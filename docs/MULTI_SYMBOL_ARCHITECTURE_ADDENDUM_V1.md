# TradePro Multi-Symbol Strategy Architecture Addendum — v1.0 (advisor, 6 Sep 2026)

Received from the owner's advisor as the developer handover for the
multi-symbol framework (MU, SNDK, WDC, NVDA, PLTR, MRVL, STX, CRDO).
Stored verbatim in intent; see the session record for the full text.
Key contracts the desk has adopted:

- ONE shared engine; every symbol is a versioned config record. Statuses:
  RESEARCH → WATCH → PAPER → ACTIVE (ACTIVE needs approved forward test +
  validated calendar + complete risk config + owner approval; Phase 1 caps
  everything at WATCH/PAPER).
- Three horizons: INTRADAY (flat before close) / SWING (flat before
  earnings) / CORE (earnings hold only with stored approval). No automatic
  conversions, ever.
- SWING_DATA_OK requires earnings_validation_status == VERIFIED; a
  conflicting future date is a BLOCKING condition (the MU 21st/30th lesson).
- Correlation buckets with per-bucket risk limits: memory_storage
  {MU, SNDK, WDC, STX}, ai_semi_infra {NVDA, MRVL, CRDO}, ai_software
  {PLTR}. A second correlated entry over the bucket limit returns
  CORRELATION_RISK_BLOCKED, not approval.
- MU v1.1: 0.8 ATR default stop / 1.0 max unapproved, structure+ATR,
  fixed-dollar stops prohibited, three-state regime incl.
  TOLERATED_SMA50_ROLLOVER, the five Phase-1 alerts, cycle expiry 30 Sep.
- SNDK: must NOT reuse MU numerics; needs its own calibration before WATCH;
  extension-from-EMA filter (do not chase a large expansion day).
- Alert hygiene: one channel, fire once per state transition, dedupe key
  includes managed_account_id, re-arm only after state exit+re-entry,
  expiry at cycle end, renewal is a reviewed decision.
- Options context: valid uses and forbidden conclusions per §7; missing =
  INSUFFICIENT.
- Build order: framework → MU cycle → INTRADAY tagging/VWAP → SNDK →
  others one at a time → per-symbol review → only then non-executable
  proposals; unattended execution needs a separate signed spec.

## Scale-invariance invariant (adopted 6 Sep 2026, external-Claude audit)

Regime-shifted names (MU 8x in a year, SNDK +236% in 26 weeks) make every
long-window statistic an average across a differently-priced asset — a
200-SMA gate sitting 67-73% below spot is a filter that cannot fail.
Therefore, for every per-symbol engine config:

- Engine lookbacks are capped at 63 sessions (EMA20 / SMA50 / ATR14 /
  20d-high are the working set).
- Engine-computed thresholds are expressed as ATR multiples or slope
  percentages, never fixed dollars or fixed percentages of price.
- OWNER-ARMED reference levels (e.g. the MU 1050 breakout watch) are exempt:
  a human may draw a dollar line; the machine may not derive one.
- Slope tests over level tests where trend must be able to fail — the SMA50
  rollover check is the working example (it fired on MU while a 200-SMA gate
  would have slept).

The population strategies (swing's 200-SMA floor) are NOT retro-edited by
this: they are pre-registered and mid-forward-test; a 63-session variant is
a v2 candidate with its own gates, not an edit.
