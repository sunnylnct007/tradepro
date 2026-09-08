using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/strangle-decisions — the durable record of every strangle evaluation.
///
/// Owner, 31 Aug 2026: "i need the stuff to be logged for analysis later on so
/// we might need a history table to store these evaluations and decisions ...
/// so we can evaluate what we did and why we did it and check if it was right
/// or not".
///
/// WHY THIS EXISTS AT ALL. The Lambda writes its ledger to /tmp, which is wiped
/// between invocations — so every scheduled decision since the move to Lambda
/// has been lost. The forward test has been recording nothing, which is the one
/// thing it was created to do.
///
/// STAND-ASIDES ARE STORED, and they are the valuable rows. This strategy's
/// edge is what it REFUSES to trade; a log of only the trades cannot tell you
/// whether the gate is set correctly. The same argument is already made in the
/// paper-record file's shadow-recording note.
/// </summary>
public static class StrangleDecisionLogEndpoints
{
    public sealed record DecisionRow(
        string Market, DateTime AsOf, string Decision, string Reason,
        DateTime? ExchangeDate = null, string? VolSymbol = null,
        decimal? VolIndex = null, decimal? VolThreshold = null,
        decimal? IvUsedPct = null, decimal? Spot = null, string? SpotBasis = null,
        bool Provisional = false, string? SessionState = null,
        string? ExpiryKind = null, int? Dte = null,
        decimal? PutStrike = null, decimal? CallStrike = null,
        decimal? Forward = null, int? Lot = null,
        decimal? Collateral = null, decimal? MarginEstimate = null,
        decimal? CreditModelled = null, string? JobsCommit = null,
        string? Detail = null, decimal? VolAtDecision = null,
        string? DataSource = null);

    /// <summary>What actually happened to a decision — placement and exit.
    /// Every field nullable: placement follows the decision, and the exit
    /// follows that by hours. "Not yet known" must be representable, because
    /// grading a row before its session closes is lookahead.</summary>
    public sealed record ExecutionRow(
        string Market, DateTime AsOf, string? ExpiryKind = null,
        bool? Placed = null, bool? Partial = null, bool? Shadow = null,
        string? BrokerOrderIds = null, decimal? CreditActual = null,
        DateTime? PlacedAtUtc = null, decimal? ExitCostActual = null,
        string? CloseTrigger = null, DateTime? ClosedAtUtc = null,
        decimal? RealisedPnl = null,
        // WHY it did not place — the broker's own words where we have them.
        // Until now this lived only in a Lambda log the owner cannot read, so
        // on screen a REFUSED placement looked identical to one never tried.
        string? PlaceError = null,
        // QUOTED, NOT TRADED — a real bid/ask mid for a strangle we did not
        // place. Separate from credit_actual on purpose: one is a fill, the
        // other is an offer, and summing them would be a lie.
        decimal? QuotedCredit = null, decimal? QuotedExit = null,
        decimal? QuotedPnl = null, DateTime? QuotedAtUtc = null,
        decimal? QuotedSpread = null,
        // Balance of the pair. Equidistant strikes are not delta-neutral once
        // skew is present; this is how we find out by how much.
        decimal? PutDelta = null, decimal? CallDelta = null,
        decimal? NetDelta = null,
        // What DELTA-mode selection WOULD have chosen (spec §2.1), recorded
        // beside what we traded. Selection itself is unchanged.
        decimal? DeltaPutStrike = null, decimal? DeltaCallStrike = null,
        decimal? DeltaModeNet = null, decimal? DeltaTarget = null,
        bool? DeltaInBand = null);

    public static IEndpointRouteBuilder MapStrangleDecisionLogEndpoints(
        this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/strangle-decisions").WithTags("StrangleDecisions");

        // POST — upsert one or many. UPSERT, not insert: a re-run (a UI
        // trigger, a retry after a failure) must not double-count, or every
        // summary computed over this table is silently wrong.
        g.MapPost("/", async (DecisionRow[] rows, NpgsqlDataSource db, CancellationToken ct) =>
        {
            if (rows is null || rows.Length == 0)
                return Results.BadRequest(new { error = "no rows" });
            await using var conn = await db.OpenConnectionAsync(ct);
            var n = await conn.ExecuteAsync(@"
                INSERT INTO strangle_decision_log
                    (market, as_of, exchange_date, decision, reason, vol_symbol,
                     vol_index, vol_threshold, iv_used_pct, spot, spot_basis,
                     provisional, session_state, expiry_kind, dte, put_strike,
                     call_strike, forward, lot, collateral, margin_estimate,
                     credit_modelled, jobs_commit, detail, vol_at_decision, data_source)
                VALUES
                    (@Market, @AsOf, @ExchangeDate, @Decision, @Reason, @VolSymbol,
                     @VolIndex, @VolThreshold, @IvUsedPct, @Spot, @SpotBasis,
                     @Provisional, @SessionState, @ExpiryKind, @Dte, @PutStrike,
                     @CallStrike, @Forward, @Lot, @Collateral, @MarginEstimate,
                     @CreditModelled, @JobsCommit, @Detail::jsonb, @VolAtDecision, @DataSource)
                -- Keyed on the session being TRADED, not the settled session the gate
                -- read. Those diverge, and keying on as_of silently destroyed a
                -- day of Indian decisions on 1 Sep 2026 (migration 073).
                ON CONFLICT (market, COALESCE(exchange_date, as_of), COALESCE(expiry_kind, '')) DO UPDATE SET
                     as_of         = EXCLUDED.as_of,
                     decision      = EXCLUDED.decision,
                     reason        = EXCLUDED.reason,
                     vol_index     = EXCLUDED.vol_index,
                     vol_threshold = EXCLUDED.vol_threshold,
                     iv_used_pct   = EXCLUDED.iv_used_pct,
                     spot          = EXCLUDED.spot,
                     spot_basis    = EXCLUDED.spot_basis,
                     provisional   = EXCLUDED.provisional,
                     session_state = EXCLUDED.session_state,
                     put_strike    = EXCLUDED.put_strike,
                     call_strike   = EXCLUDED.call_strike,
                     forward       = EXCLUDED.forward,
                     collateral    = EXCLUDED.collateral,
                     margin_estimate = EXCLUDED.margin_estimate,
                     credit_modelled = EXCLUDED.credit_modelled,
                     vol_at_decision = EXCLUDED.vol_at_decision,
                     data_source     = EXCLUDED.data_source,
                     jobs_commit   = EXCLUDED.jobs_commit,
                     detail        = EXCLUDED.detail,
                     decided_at_utc = now();", rows);
            return Results.Ok(new { ok = true, rows = n });
        });

        // POST /execution — record what ACTUALLY happened to a decision.
        //
        // Owner, 31 Aug 2026: "f the strangell worked or not". The platform
        // could not answer that from its own records — the decision log stops
        // at the decision. Nothing said whether the order was placed, what we
        // were FILLED at, or what it cost to close, so both figures had to be
        // reconstructed from the broker by hand.
        //
        // Keyed on the SAME (market, as_of, expiry_kind) the decision upsert
        // uses, so an execution can only ever attach to a decision that was
        // genuinely recorded first. An execution with no decision is REFUSED
        // rather than inserted: a fill with no recorded reasoning is exactly
        // the row that makes a forward test unauditable.
        g.MapPost("/execution", async (ExecutionRow row, NpgsqlDataSource db,
                                       CancellationToken ct) =>
        {
            if (row is null || string.IsNullOrWhiteSpace(row.Market))
                return Results.BadRequest(new { error = "market required" });

            await using var conn = await db.OpenConnectionAsync(ct);
            var n = await conn.ExecuteAsync(@"
                UPDATE strangle_decision_log SET
                    placed           = COALESCE(@Placed, placed),
                    partial          = COALESCE(@Partial, partial),
                    shadow           = COALESCE(@Shadow, shadow),
                    broker_order_ids = COALESCE(@BrokerOrderIds, broker_order_ids),
                    credit_actual    = COALESCE(@CreditActual, credit_actual),
                    placed_at_utc    = COALESCE(@PlacedAtUtc, placed_at_utc),
                    exit_cost_actual = COALESCE(@ExitCostActual, exit_cost_actual),
                    close_trigger    = COALESCE(@CloseTrigger, close_trigger),
                    closed_at_utc    = COALESCE(@ClosedAtUtc, closed_at_utc),
                    realised_pnl     = COALESCE(@RealisedPnl, realised_pnl),
                    -- CLEARED ON SUCCESS. COALESCE alone never unsets, so a
                    -- refusal from an earlier run of the same session survived
                    -- a later successful placement: on 8 Sep 2026 the SPX row
                    -- read placed=true with credit_actual 5,316.74 AND a
                    -- PROVISIONAL-strikes refusal from the 04:10 attempt. A row
                    -- carrying both is a row nobody can read.
                    -- (No double quotes in this block: it is a C# verbatim
                    -- string and a bare quote ends it. Second time — the same
                    -- mistake produced 110 compile errors on 1 Sep.)
                    place_error      = CASE WHEN @Placed IS TRUE THEN NULL
                                            ELSE COALESCE(@PlaceError, place_error) END,
                    quoted_credit    = COALESCE(@QuotedCredit, quoted_credit),
                    quoted_exit      = COALESCE(@QuotedExit, quoted_exit),
                    quoted_pnl       = COALESCE(@QuotedPnl, quoted_pnl),
                    quoted_at_utc    = COALESCE(@QuotedAtUtc, quoted_at_utc),
                    quoted_spread    = COALESCE(@QuotedSpread, quoted_spread),
                    put_delta        = COALESCE(@PutDelta, put_delta),
                    call_delta       = COALESCE(@CallDelta, call_delta),
                    net_delta        = COALESCE(@NetDelta, net_delta),
                    delta_put_strike  = COALESCE(@DeltaPutStrike, delta_put_strike),
                    delta_call_strike = COALESCE(@DeltaCallStrike, delta_call_strike),
                    delta_mode_net    = COALESCE(@DeltaModeNet, delta_mode_net),
                    delta_target      = COALESCE(@DeltaTarget, delta_target),
                    delta_in_band     = COALESCE(@DeltaInBand, delta_in_band)
                WHERE market = @Market
                  -- SAME KEY AS THE DECISION UPSERT. Migration 073 moved that to
                  -- the TRADED session (exchange_date); this still matched as_of,
                  -- so on 2 Sep 2026 four legs closed successfully and not one
                  -- exit was recorded — the write found no row and 404'd, and
                  -- the round trip stayed unanswerable. Two keys for one row.
                  AND COALESCE(exchange_date, as_of) = @AsOf
                  AND COALESCE(expiry_kind, '') = COALESCE(@ExpiryKind, '');", row);

            if (n == 0)
                // FAIL LOUD. Silently inserting would create a fill with no
                // recorded reasoning — unauditable, and worse than no row.
                return Results.Json(new
                {
                    ok = false,
                    error = $"no decision recorded for {row.Market} {row.AsOf:yyyy-MM-dd} "
                          + $"[{row.ExpiryKind}] — an execution cannot be attached to a "
                          + "decision that was never logged",
                }, statusCode: 404);

            return Results.Ok(new { ok = true, updated = n });
        });

        // GET — the history, newest first. `market` and `decision` narrow it;
        // `days` bounds it. Stand-asides are INCLUDED by default, deliberately.
        g.MapGet("/", async (NpgsqlDataSource db, string? market, string? decision,
                             int days, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = await conn.QueryAsync(@"
                SELECT market, as_of, exchange_date, decided_at_utc, decision, reason,
                       vol_symbol, vol_index::float8 AS vol_index,
                       vol_threshold::float8 AS vol_threshold,
                       spot::float8 AS spot, spot_basis, provisional, session_state,
                       expiry_kind, dte, put_strike::float8 AS put_strike,
                       call_strike::float8 AS call_strike,
                       forward::float8 AS forward,
                       vol_at_decision::float8 AS vol_at_decision, data_source,
                       collateral::float8 AS collateral,
                       -- The MONEY columns were written but never selected, so
                       -- credit_modelled and lot read as NULL through the API
                       -- while sitting populated in the table. Asked how much
                       -- the system would have made, the log could not answer
                       -- from data it already held. (No double quotes in here:
                       -- this is a C# verbatim string and a bare quote ends it.)
                       margin_estimate::float8 AS margin_estimate,
                       credit_modelled::float8 AS credit_modelled, lot,
                       -- EXECUTION — added in 072 and likewise never selected,
                       -- which made the whole decision->execution link
                       -- invisible to every reader of this endpoint.
                       placed, partial, shadow, broker_order_ids,
                       credit_actual::float8 AS credit_actual, placed_at_utc,
                       exit_cost_actual::float8 AS exit_cost_actual,
                       close_trigger, closed_at_utc,
                       realised_pnl::float8 AS realised_pnl, place_error,
                       quoted_credit::float8 AS quoted_credit,
                       quoted_exit::float8   AS quoted_exit,
                       quoted_pnl::float8    AS quoted_pnl,
                       quoted_spread::float8 AS quoted_spread, quoted_at_utc,
                       put_delta::float8  AS put_delta,
                       call_delta::float8 AS call_delta,
                       net_delta::float8  AS net_delta,
                       delta_put_strike::float8  AS delta_put_strike,
                       delta_call_strike::float8 AS delta_call_strike,
                       delta_mode_net::float8    AS delta_mode_net,
                       delta_target::float8      AS delta_target, delta_in_band,
                       index_close::float8 AS index_close,
                       outcome_pct::float8 AS outcome_pct, outcome_note, graded_at_utc,
                       jobs_commit
                  FROM strangle_decision_log
                 WHERE (@market IS NULL OR market = @market)
                   AND (@decision IS NULL OR decision = @decision)
                   -- FILTER ON THE KEY, NOT ON as_of. The upsert above keys on
                   -- COALESCE(exchange_date, as_of) -- the session being TRADED --
                   -- but this SELECT filtered on as_of, the settled session the
                   -- gate READ. Those diverge across a weekend or a holiday.
                   --
                   -- 8 Sep 2026: US rows for exchange_date 2026-09-08 carried
                   -- as_of 2026-09-04, because 7 Sep was Labor Day. The close
                   -- job asks for days=3, so the window began 2026-09-05 and
                   -- every US row fell outside it. The job read zero placed
                   -- rows while four pairs sat open at the broker, concluded
                   -- they must have been opened on an earlier session, and
                   -- flattened all four SEVEN MINUTES after they were opened.
                   --
                   -- Migration 073 fixed exactly this divergence in the UPSERT
                   -- and left the SELECT reading the other column. Half a fix
                   -- reads as a whole one until the calendar separates them.
                   AND COALESCE(exchange_date, as_of) >= (CURRENT_DATE - (@days || ' days')::interval)
                 ORDER BY COALESCE(exchange_date, as_of) DESC, market
                 LIMIT 2000;",
                new { market, decision, days = days <= 0 ? 90 : days });
            return Results.Ok(new { rows = rows.AsList() });
        });

        // GET /summary — "was it right?", per market.
        //
        // Reports traded AND declined counts side by side. A summary that shows
        // only what was traded cannot answer whether the gate was set correctly,
        // which is the actual question being asked of this table.
        g.MapGet("/summary", async (NpgsqlDataSource db, int days, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = await conn.QueryAsync(@"
                SELECT market,
                       COUNT(*)                                        AS evaluated,
                       COUNT(*) FILTER (WHERE decision = 'CANDIDATE')  AS traded,
                       COUNT(*) FILTER (WHERE decision = 'STAND_ASIDE') AS declined,
                       COUNT(*) FILTER (WHERE provisional)             AS provisional,
                       COUNT(*) FILTER (WHERE graded_at_utc IS NOT NULL) AS graded,
                       AVG(outcome_pct)::float8                        AS mean_outcome_pct,
                       MIN(outcome_pct)::float8                        AS worst_outcome_pct
                  FROM strangle_decision_log
                 -- Same key as the upsert and the row SELECT. This was the
                 -- THIRD site reading as_of while the table is keyed on the
                 -- traded session; it was found only by looking for the other
                 -- two. Fix every site or the next holiday finds the one left.
                 WHERE COALESCE(exchange_date, as_of) >= (CURRENT_DATE - (@days || ' days')::interval)
                 GROUP BY market
                 ORDER BY market;",
                new { days = days <= 0 ? 90 : days });
            return Results.Ok(new
            {
                rows = rows.AsList(),
                note = "declined rows are included on purpose — the gate is the "
                     + "strategy, and only the refusals show whether it is set right",
            });
        });

        // ── LIVE DESK P&L ────────────────────────────────────────────────
        //
        // Owner, 8 Sep 2026: "we shd be able to see live pnl at any point of
        // time." Both halves of the number already existed and neither was
        // ever added up: realised sits in this table, and the open position is
        // marked continuously by the broker. The screen said "P&L lands when
        // the position closes" while a live mark sat one panel above it.
        //
        // Served as an ENDPOINT, not only on screen, because the owner reviews
        // this desk through an agent and an agent cannot read a screen.
        //
        // Deliberately does NOT attribute legs to markets. That mapping
        // (market -> broker root, SPX/SPXW, GOLD -> GLD) is defined once, in
        // the strategy config, and a second copy here is the exact shape of
        // bug that has cost this desk the most: one value, two definitions,
        // one of them fixed. Every leg is returned with its contract so the
        // caller can attribute; the TOTAL never depends on attribution.
        g.MapGet("/pnl", async (
            NpgsqlDataSource db,
            TradePro.Api.Providers.IBKR.IBKRClient ibkr,
            CancellationToken ct,
            int days = 1) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var closed = (await conn.QueryAsync(@"
                SELECT market,
                       COALESCE(exchange_date, as_of)::date AS session,
                       shadow,
                       realised_pnl::float8   AS realised_pnl,
                       credit_actual::float8  AS credit_actual
                  FROM strangle_decision_log
                 WHERE placed IS TRUE
                   AND realised_pnl IS NOT NULL
                   AND COALESCE(exchange_date, as_of) >= (CURRENT_DATE - (@days || ' days')::interval)
                 ORDER BY market;",
                new { days = days <= 0 ? 1 : days })).AsList();

            var realised = closed.Sum(r => (double)(r.realised_pnl ?? 0d));

            // The open half. A broker that cannot be reached is reported as
            // such — it is NOT zero. A zero here would read as "flat", which
            // is the single most dangerous thing this endpoint could say.
            double? unrealised = null;
            var legRows = new List<object>();
            var unmarkable = new List<object>();
            string? openError = null;

            if (!ibkr.IsEnabled)
            {
                openError = "IBKR is not enabled — the open half of this number is UNKNOWN, not zero";
            }
            else
            {
                try
                {
                    var pos = await ibkr.GetPositionsAsync(ct, forceFresh: true);
                    // The broker's OWN error, surfaced rather than swallowed. A
                    // failed read that falls through to an empty list would
                    // report the book as flat.
                    if (pos.Error is not null)
                        throw new InvalidOperationException(pos.Error);
                    // fresh: true is not optional. IBKR serves positions from
                    // its own cache, and a CLOSED position comes back as a
                    // qty-0 row, so both filters below are load-bearing.
                    var legs = pos.Positions.Where(p => string.Equals(p.AssetClass, "OPT",
                                                  StringComparison.OrdinalIgnoreCase)
                                           && p.Quantity != 0m).ToList();
                    double sum = 0;
                    foreach (var p in legs)
                    {
                        var mult = p.Multiplier is decimal m && m > 0 ? m : 100m;
                        var contract = p.ContractDesc ?? p.Symbol ?? $"conid {p.ConId}";
                        if (p.UnrealizedPnl is decimal u)
                        {
                            sum += (double)u;
                            legRows.Add(new
                            {
                                contract,
                                conid = p.ConId,
                                quantity = p.Quantity,
                                soldAt = p.AvgCost is decimal ac ? ac / mult : (decimal?)null,
                                markedAt = p.MarketPrice,
                                unrealised = u,
                            });
                        }
                        else
                        {
                            // No mark = no number. Listed by name so the gap is
                            // legible instead of quietly missing from a total.
                            unmarkable.Add(new { contract, conid = p.ConId, quantity = p.Quantity });
                        }
                    }
                    unrealised = sum;
                }
                catch (Exception ex)
                {
                    openError = $"could not read positions from the broker: {ex.Message}";
                }
            }

            // A total is offered ONLY when both halves are known. Adding a
            // known realised figure to an unknown open one produces a number
            // that looks complete and is not.
            double? total = unrealised is double u2 ? realised + u2 : null;

            var warnings = new List<string>();
            if (openError is not null) warnings.Add(openError);
            if (unmarkable.Count > 0)
                warnings.Add($"{unmarkable.Count} open leg(s) have NO broker mark and are "
                           + "excluded from the open figure — the total understates the book");
            // One row per market per session cannot hold two round-trips. When
            // a session shows a realised result AND a position is still open,
            // the two may belong to different trades in the same day.
            if (closed.Count > 0 && legRows.Count > 0)
                warnings.Add("a session here has BOTH a realised result and an open position: "
                           + "the decision log holds one row per market per session, so these "
                           + "may be two different round-trips and must not be read as one");

            return Results.Ok(new
            {
                asOfUtc = DateTime.UtcNow,
                days = days <= 0 ? 1 : days,
                broker = ibkr.IsEnabled ? ibkr.BrokerLabel : null,
                realised = new
                {
                    total = realised,
                    pairs = closed.Count,
                    gated = closed.Where(r => r.shadow != true)
                                  .Sum(r => (double)(r.realised_pnl ?? 0d)),
                    shadow = closed.Where(r => r.shadow == true)
                                   .Sum(r => (double)(r.realised_pnl ?? 0d)),
                },
                open = new
                {
                    unrealised,
                    legs = legRows.Count,
                    detail = legRows,
                    unmarkable,
                },
                total,
                warnings,
                note = "realised comes from the decision log; open is marked by the broker "
                     + "on every request. total is null unless BOTH halves are known.",
            });
        });

        return app;
    }
}
