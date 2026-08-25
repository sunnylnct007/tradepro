"""G5 on FULLY corrected data — both manifests, all four fields. 25 Aug 2026.

The third and final revision of G5 today, and the first computed on data that
is corrected from BOTH directions.

WHAT THIS CHECK CANNOT FAIL ON, stated up front because that is the lesson of
the day: 79 of the 244 symbols have NO API coverage, so nothing here speaks for
them; and it sees only instrument-dates that BOTH stores hold. It is a
measurement over the covered set, not over the universe.

## Two manifests, and neither alone is enough

    BAD_BARS_IBKR_SOCKET.json   25,178 rows / 121 symbols   source == "ibkr"
    ISOLATED_SEAM_BARS.json         43 rows /   8 symbols   yfinance seams

    as stored     trades 2503  win 73.2%  mean +1.10%  G4 17.8%  G5 -23.2%
    + ibkr only   trades 2517  win 73.2%  mean +1.11%  G4 18.1%  G5 -23.2%
    + seam only   trades 2508  win 73.2%  mean +1.08%  G4 17.5%  G5 -17.7%
    BOTH          trades 2522  win 73.2%  mean +1.09%  G4 17.9%  G5 -21.3%

Correcting only the ibkr rows leaves G5 at -23.2% because the worst trade is
HYG, a YFINANCE seam. Correcting only the seams gives -17.7% because BROS is
still masked by a bad ibkr low. Each manifest hides the other's worst case.

## G5 = -21.3%, and the store was FLATTERING the tail

BROS, signal 2024-07-24, entry 36.96, stop 34.00:

    2024-08-07   stored low 28.80   API low 37.50   <- stop hit on a FALSE low
    2024-08-08   stored low 26.96   API low 26.96   <- the real collapse

The stored low on 7 Aug is 23% below the truth. It triggered the stop a day
EARLY, at a better price, and closed the trade before the real fall on the 8th.
Correcting it lets the position survive into a genuine -21.3% loss.

**That is the direction I pre-registered as the more worrying one.** A bad bar
that manufactures a loss gets caught because the number looks implausible — I
caught HYG's -23.2% that way this morning. A bad bar that HIDES a loss makes
the strategy look safer, and nothing about a good number invites scrutiny.

## Consequence for the record

G5 has been revised three times today and every revision was a correction to
MY figure, not a change in the strategy:

    -23.2%   raw store              phantom HYG bar, an artefact
    -17.7%   whole-path exclusion   real, but computed on a partial correction
    -21.3%   both manifests, OHLC   the current best measurement

All six gates still PASS. **But G5's margin is 3.7 points, not the 7.3 I told
the owner this morning.** That statement is superseded. The strategy is safer
than the -23.2% it was carrying at the start of the day and less safe than the
-17.7% I claimed at midday, and the honest summary is that a -8% stop on this
universe produces about a -21% worst case once the data is right.
