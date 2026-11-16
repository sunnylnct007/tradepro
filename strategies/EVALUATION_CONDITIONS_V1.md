# Daily evaluation — condition tags (fixed 16 Nov 2026, before any result)

Owner, 16 Nov 2026: "the whole purpose of tradepro is to evaluate diff
strategy", "one fit for all doesn't work", "stock having sector sell off is
diff then stock having no sector sell off". Every strategy's daily result is
tagged with these conditions so results build up BY CONDITION. The tags are
fixed here, before any result is seen, and are never re-cut afterwards — a
condition discovered by slicing results is not evidence.

All from settled daily closes of the session being evaluated.

| Tag | Definition |
|---|---|
| market_trend | `up` if SPY closes above its 200-day average, else `down` |
| day_type | QQQ close-to-close: `Up` > +0.5%, `Down` < -0.5%, else `Flat` |
| volatility | SPY 20-session realised volatility, annualised: `calm` < 15%, `normal` 15-25%, `stressed` > 25% |
| sector_selloff (per sector) | the sector's SPDR ETF 5-session return <= -3% |
| semis_selloff | SMH 5-session return <= -3% (once SMH has history) |

Sector ETFs: XLI, XLV, XLK, XLF, XLC, XLY, XLP, XLE, XLRE, XLB, XLU (as
momentum_rotation.SECTOR_ETF). A holding is "in a sector sell-off" on a day its
own sector is tagged.

Changing a definition means a V2 file and a new record from that date; the V1
record is kept as it is.
