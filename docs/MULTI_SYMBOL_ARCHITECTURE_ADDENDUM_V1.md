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
