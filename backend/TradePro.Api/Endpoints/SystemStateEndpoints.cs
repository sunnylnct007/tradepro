using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/system/state — kill switch + system mode.
///
/// One central source of truth for "are we trading?" Every order
/// dispatch path will read this before sending. The risk module
/// (step 5) wraps the same check + adds its own automated triggers.
///
/// Three modes (see migration 017 for rationale):
///   normal  — operating normally
///   frozen  — no new BUYs, defensive SELLs still allowed
///   panic   — refuse every order
/// </summary>
public static class SystemStateEndpoints
{
    public static IEndpointRouteBuilder MapSystemStateEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/system").WithTags("System");

        // GET /api/system/state — current mode + when/who set it.
        // Banner reads this on every page; orders dispatch reads it
        // before placing. Cheap (single-row table).
        group.MapGet("/state", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstOrDefaultAsync<SystemStateRow>(@"
                SELECT mode, reason, set_at_utc AS SetAtUtc, set_by AS SetBy
                FROM system_state WHERE id = 1;");
            if (row is null)
            {
                return Results.Ok(new
                {
                    mode = "normal", reason = (string?)null,
                    setAtUtc = (DateTime?)null, setBy = (string?)null,
                });
            }
            return Results.Ok(new
            {
                mode = row.Mode,
                reason = row.Reason,
                setAtUtc = row.SetAtUtc,
                setBy = row.SetBy,
                isTradingFrozen = row.Mode != "normal",
                isPanic = row.Mode == "panic",
            });
        });

        // POST /api/system/freeze   { reason }
        // POST /api/system/panic    { reason }
        // POST /api/system/resume   { reason? }
        //
        // Each one is a state transition. Body is required for
        // freeze/panic (operator must provide context for the audit
        // log); optional for resume (default reason: "manual resume").
        // All transitions are append-logged to system_state_events.
        group.MapPost("/freeze", (ChangeBody body, HttpContext ctx, NpgsqlDataSource db)
            => SetModeAsync("frozen", body, ctx, db));
        group.MapPost("/panic", (ChangeBody body, HttpContext ctx, NpgsqlDataSource db)
            => SetModeAsync("panic", body, ctx, db));
        group.MapPost("/resume", (ChangeBody? body, HttpContext ctx, NpgsqlDataSource db)
            => SetModeAsync("normal", body, ctx, db));

        // GET /api/system/state/history?limit=
        // Recent mode-change events for the audit panel. Explicit
        // historical surface per the no-clutter principle — main
        // /state endpoint only ever shows current.
        group.MapGet("/state/history", async (int? limit, NpgsqlDataSource db) =>
        {
            var lim = Math.Clamp(limit ?? 50, 1, 200);
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync<SystemEventRow>(@"
                SELECT id, prior_mode AS PriorMode, new_mode AS NewMode,
                       reason, changed_at_utc AS ChangedAtUtc,
                       changed_by AS ChangedBy
                FROM system_state_events
                ORDER BY changed_at_utc DESC
                LIMIT @lim;", new { lim });
            return Results.Ok(new
            {
                events = rows.Select(r => new
                {
                    id = r.Id, priorMode = r.PriorMode, newMode = r.NewMode,
                    reason = r.Reason,
                    changedAtUtc = r.ChangedAtUtc,
                    changedBy = r.ChangedBy,
                }),
            });
        });

        return app;
    }

    private static async Task<IResult> SetModeAsync(
        string newMode, ChangeBody? body, HttpContext ctx, NpgsqlDataSource db)
    {
        // freeze + panic require an explicit reason — defensive UX.
        // Operator should never freeze "by accident."
        var requireReason = newMode != "normal";
        var reason = body?.Reason?.Trim();
        if (requireReason && string.IsNullOrWhiteSpace(reason))
        {
            return Results.BadRequest(new
            {
                error = $"reason is required when setting mode to {newMode}",
            });
        }
        var who = body?.SetBy ?? ctx.User?.Identity?.Name ?? "ui";

        await using var conn = await db.OpenConnectionAsync();
        await using var tx = await conn.BeginTransactionAsync();
        try
        {
            var prior = await conn.QueryFirstOrDefaultAsync<string>(
                "SELECT mode FROM system_state WHERE id = 1 FOR UPDATE;",
                transaction: tx) ?? "normal";
            if (prior == newMode)
            {
                await tx.RollbackAsync();
                return Results.Ok(new
                {
                    mode = newMode, noop = true,
                    message = $"already in {newMode} mode",
                });
            }
            await conn.ExecuteAsync(@"
                UPDATE system_state
                SET mode = @newMode, reason = @reason,
                    set_at_utc = NOW(), set_by = @who
                WHERE id = 1;",
                new { newMode, reason = reason ?? "manual resume", who },
                transaction: tx);
            await conn.ExecuteAsync(@"
                INSERT INTO system_state_events
                  (prior_mode, new_mode, reason, changed_by)
                VALUES (@prior, @newMode, @reason, @who);",
                new { prior, newMode, reason, who },
                transaction: tx);
            await tx.CommitAsync();
        }
        catch
        {
            await tx.RollbackAsync();
            throw;
        }
        return Results.Ok(new
        {
            mode = newMode, priorMode = "captured-in-events",
            reason, setBy = who,
        });
    }

    private sealed record SystemStateRow(
        string Mode, string? Reason, DateTime SetAtUtc, string SetBy);

    private sealed record SystemEventRow(
        long Id, string PriorMode, string NewMode, string? Reason,
        DateTime ChangedAtUtc, string ChangedBy);

    public sealed record ChangeBody(string? Reason, string? SetBy);
}
