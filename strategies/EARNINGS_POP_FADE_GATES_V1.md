# Earnings pop-and-fade — gates V1 (pre-registered 28 Nov 2026, before any result)

Question: when a stock gaps UP on earnings while its sector is weak, does the
pop fade often enough to sell a call spread above it — and is that different
from when the sector is strong?

## Data
- Daily OHLC from TradePro get_candles (2022-06-01 → 2026-11-27).
- Report dates from TradePro get_earnings_calendar (store; yfinance_hist + finnhub).
- Universe (23): WDC NVDA STX MDB SNOW DELL AMD META INTC HOOD APLD AMZN TSLA
  AAPL MRVL SKHY CRWV QCOM CRDO PLTR GOOGL ORCL MU.
- Sector ETF: SMH for WDC NVDA STX DELL AMD INTC MRVL SKHY QCOM CRDO MU APLD;
  IGV for MDB SNOW PLTR ORCL CRWV; SPY for META HOOD AMZN TSLA AAPL GOOGL.

## Event definition
- Reaction day R: report day if session = bmo; next trading day if amc;
  if session unknown, whichever of T / T+1 has the larger |open − prior close|.
- Duplicate report dates within 10 trading days: keep the later one.
- POP: open(R) / close(R−1) − 1 ≥ +4%.
- Sector WEAK: sector ETF close(R−1) below its 50-day simple average. Else STRONG.

## Outcomes (measured per pop)
- O1 day fade: close(R) < open(R).
- O2 week fade: close(R+5) < close(R).
- O3 return close(R) → close(R+5) and → close(R+10).
- O4 call-spread breach: max close over R+1…R+10 > 1.07 × close(R)
  (proxy for a short call ~7% above the entry close, ~2 weeks to expiry).
- Filtered entry F: only pops where O1 holds (the gap failed on the day), entry at close(R).

## Gates (all must pass for "use it")
- G1: WEAK pops, P(O2) ≥ 60%, and ≥ 15 points above STRONG pops.
- G2: at least 20 WEAK pops.
- G3: WEAK + filter F, breach rate O4 ≤ 15%.
- G4: G1's direction (WEAK fade rate > STRONG) holds in both halves of the sample
  (events before / after 2024-09-01).

No re-tuning of thresholds after seeing results. A fail is reported as a fail.

## Amendments before any result was computed (28 Nov, data-handling only)
- The store lists provisional finnhub dates next to the real one (e.g. NVDA 17 and
  25 Nov). Rule used instead of "keep the later": keep yfinance history dates and
  only finnhub rows uploaded AFTER their report date (i.e. confirmed). All are amc.
- SKHY dropped: the store has bars only from Jul 2026 and no confirmed print in range.
- CRDO and CRWV bars start 2025; included with what exists.
