using Dapper;
using Npgsql;
using TradePro.Api.Models;
using TradePro.Api.Providers;

namespace TradePro.Api.Endpoints;

public static class MarketDataEndpoints
{
    public static IEndpointRouteBuilder MapMarketDataEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/marketdata").WithTags("MarketData");

        group.MapGet("/providers", (IMarketDataRegistry registry) =>
            Results.Ok(new { providers = registry.AvailableProviders }));

        group.MapGet("/candles", async (
            string symbol,
            string? provider,
            string? interval,
            DateTime? from,
            DateTime? to,
            IMarketDataRegistry registry,
            NpgsqlDataSource db,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(symbol))
                return Results.BadRequest(new { error = "symbol is required" });

            var iv = interval ?? "1d";
            var p = registry.Resolve(provider);
            var fromDate = from ?? DateTime.UtcNow.AddYears(-1);
            var toDate = to ?? DateTime.UtcNow;
            var series = await p.GetCandlesAsync(symbol, iv, fromDate, toDate, ct);

            // The default daily provider (Yahoo) lags the newest session — on a
            // Saturday, Friday's bar is often still absent, so the chart sits a
            // session behind ("why no 24th?"). The central ibkr_price_bars store
            // harvests a resolution='1d' series that carries the latest session
            // (source=ibkr). Fill ONLY the missing tail (bars strictly newer than
            // the provider's last) so the chart is never behind, without touching
            // the long history the provider owns. Daily interval only.
            if (IsDaily(iv))
                series = await MergeFresherDailyTailAsync(series, symbol, toDate, db, ct);

            return Results.Ok(series);
        });

        // Historical earnings announcements for the PriceHistoryChart overlay.
        // Returns reported earnings only (EPS actual present); excludes upcoming.
        // Defaults to 5-year lookback (1825 days) — matches the chart default.
        // Empty on any failure so the chart degrades to "no markers" cleanly.
        group.MapGet("/earnings", async (
            string symbol,
            int? lookbackDays,
            YahooFinanceProvider yahoo,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(symbol))
                return Results.BadRequest(new { error = "symbol is required" });

            var days = lookbackDays is > 0 ? lookbackDays.Value : 1825;
            var markers = await yahoo.GetEarningsMarkersAsync(symbol, days, ct);

            // Serialise with snake_case field names to match the Python layer's
            // historical_earnings shape (date / eps_actual / eps_estimate / surprise_pct).
            var payload = markers.Select(m => new
            {
                date = m.Date,
                eps_actual = m.EpsActual,
                eps_estimate = m.EpsEstimate,
                surprise_pct = m.SurprisePct,
            });

            return Results.Ok(new
            {
                symbol,
                lookback_days = days,
                earnings = payload,
            });
        });

        // Insider purchase overlay for PriceHistoryChart. Only BUY transactions
        // are surfaced — insider sales are too noisy (10b5-1 auto-sell plans,
        // tax, diversification). "I" green chips on the chart at each buy date.
        // Default 365d lookback (insider data is recent by nature).
        group.MapGet("/insiders", async (
            string symbol,
            int? lookbackDays,
            YahooFinanceProvider yahoo,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(symbol))
                return Results.BadRequest(new { error = "symbol is required" });

            var days = lookbackDays is > 0 ? lookbackDays.Value : 365;
            var trades = await yahoo.GetInsiderBuysAsync(symbol, days, ct);

            var payload = trades.Select(t => new
            {
                date = t.Date,
                name = t.Name,
                title = t.Title,
                shares = t.Shares,
                value = t.Value,
            });

            return Results.Ok(new
            {
                symbol,
                lookback_days = days,
                trades = payload,
            });
        });

        // Corporate actions overlay (dividends + splits) for PriceHistoryChart.
        // Returns events oldest-first within lookbackDays (default 1825 d = 5y).
        // Dividends show as "D" chips, splits as "S" chips on the price chart.
        group.MapGet("/corporate-actions", async (
            string symbol,
            int? lookbackDays,
            YahooFinanceProvider yahoo,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(symbol))
                return Results.BadRequest(new { error = "symbol is required" });

            var days = lookbackDays is > 0 ? lookbackDays.Value : 1825;
            var actions = await yahoo.GetCorporateActionsAsync(symbol, days, ct);

            var payload = actions.Select(a => new
            {
                date = a.Date,
                type = a.Type,
                amount = a.Amount,
                ratio = a.Ratio,
            });

            return Results.Ok(new
            {
                symbol,
                lookback_days = days,
                actions = payload,
            });
        });

        return app;
    }

    private static bool IsDaily(string interval)
    {
        var i = (interval ?? "").Trim().ToLowerInvariant();
        return i is "" or "1d" or "1day" or "day" or "daily" or "d";
    }

    /// <summary>
    /// Append daily bars from the central ibkr_price_bars store (resolution='1d')
    /// that are NEWER than the provider series' last bar — filling the tail the
    /// lagging daily provider hasn't caught up to yet. The store is harvested
    /// continuously (IBKR primary), so it carries the freshest completed session.
    /// Fail-open: any error (missing table, cast, empty) returns the series
    /// unchanged — this only ever ADDS the missing newest session, never rewrites
    /// history, so it can't corrupt an existing chart or backtest.
    /// </summary>
    private static async Task<CandleSeries> MergeFresherDailyTailAsync(
        CandleSeries series, string symbol, DateTime toDate,
        NpgsqlDataSource db, CancellationToken ct)
    {
        try
        {
            if (series.Candles.Count == 0) return series;
            var lastTs = series.Candles[^1].Timestamp;

            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = (await conn.QueryAsync(@"
                SELECT ts, open, high, low, close, volume
                FROM ibkr_price_bars
                WHERE symbol = @symbol AND resolution = '1d'
                  AND ts > @lastTs AND ts <= @toDate
                ORDER BY ts ASC;",
                new { symbol, lastTs, toDate })).AsList();
            if (rows.Count == 0) return series;

            var merged = new List<Candle>(series.Candles);
            foreach (var r in rows)
            {
                merged.Add(new Candle(
                    Timestamp: (DateTime)r.ts,
                    Open: (decimal)r.open,
                    High: (decimal)r.high,
                    Low: (decimal)r.low,
                    Close: (decimal)r.close,
                    AdjustedClose: null,          // store carries raw prices, no split-adj stream
                    Volume: (long)r.volume));
            }
            return series with
            {
                Candles = merged,
                Provider = $"{series.Provider}+ibkr_store_tail({rows.Count})",
            };
        }
        catch
        {
            return series;   // never let the tail-fill break the base series
        }
    }
}
