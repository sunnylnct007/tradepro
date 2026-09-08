using System.Text.RegularExpressions;
using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// Every table an endpoint queries must actually exist in a migration.
///
/// 8 Sep 2026: the desk-stats endpoint shipped reading strangle_manual_tradeS.
/// The ROUTE is /strangle-manual-trades; the TABLE is strangle_manual_trade.
/// The name was taken from the route instead of from the SQL, the endpoint
/// returned a 500 on every call, and nothing caught it — the tests around it
/// assert on source text and never execute a query, and the DB-backed tests do
/// not run in every environment.
///
/// This is the cheap middle ground: no database, but the identifiers are real.
/// </summary>
public class SqlTableNamesExistTest
{
    private static readonly HashSet<string> Keywords =
        new(StringComparer.OrdinalIgnoreCase) { "select", "set", "values", "unnest", "generate_series", "true", "false", "null" };

    private static DirectoryInfo Root()
    {
        var d = new DirectoryInfo(AppContext.BaseDirectory);
        while (d is not null
               && !Directory.Exists(Path.Combine(d.FullName, ".git"))
               && !File.Exists(Path.Combine(d.FullName, ".git")))
            d = d.Parent;
        Assert.NotNull(d);
        return d!;
    }

    [Fact]
    public void EveryTableQueriedByAnEndpointExistsInAMigration()
    {
        var root = Root();
        var migrations = Directory.GetFiles(
            Path.Combine(root.FullName, "backend/TradePro.Api/db/migrations"), "*.sql");

        // Everything a migration ever creates. Matching CREATE TABLE (and the
        // IF NOT EXISTS form) rather than any mention, so a table named only in
        // a comment does not count as existing.
        var declared = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var f in migrations)
            foreach (Match m in Regex.Matches(File.ReadAllText(f),
                         @"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)",
                         RegexOptions.IgnoreCase))
                declared.Add(m.Groups[1].Value);

        // Views count too — an endpoint may legitimately read one.
        foreach (var f in migrations)
            foreach (Match m in Regex.Matches(File.ReadAllText(f),
                         @"CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+([a-z_][a-z0-9_]*)",
                         RegexOptions.IgnoreCase))
                declared.Add(m.Groups[1].Value);

        Assert.NotEmpty(declared);

        var missing = new List<string>();
        foreach (var f in Directory.GetFiles(
                     Path.Combine(root.FullName, "backend/TradePro.Api/Endpoints"), "*.cs"))
        {
            // ONLY the SQL. An earlier version scanned the whole file and
            // matched English in C# comments -- "apart FROM the desk", "INTO a
            // total" -- which is a test that fails on prose. SQL in this
            // codebase lives in @"..." verbatim strings; nothing else is a query.
            foreach (Match lit in Regex.Matches(File.ReadAllText(f),
                         "@\"(?:[^\"]|\"\")*\"", RegexOptions.Singleline))
            {
                // Inside the SQL, -- comments are still prose.
                var sql = Regex.Replace(lit.Value, @"--.*$", "", RegexOptions.Multiline);
                // FROM is not always a table: extract(epoch FROM ts),
                // substring(x FROM y), trim(both FROM s) all use it as syntax.
                sql = Regex.Replace(
                    sql, @"\b(?:extract|substring|trim|position|overlay)\s*\([^)]*?\bfrom\b",
                    " ", RegexOptions.IgnoreCase);

                // Names the query declares for ITSELF are not tables: CTEs
                // (WITH base AS (...)) and subquery aliases () AS ts). They
                // resolve, so demanding a migration for them is a false alarm
                // -- and a test that cries wolf gets muted, which costs more
                // than the test was worth.
                var local = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (Match c in Regex.Matches(sql, @"([a-z_][a-z0-9_]*)\s+AS\s*\(",
                                                  RegexOptions.IgnoreCase))
                    local.Add(c.Groups[1].Value);
                foreach (Match c in Regex.Matches(sql, @"\)\s*(?:AS\s+)?([a-z_][a-z0-9_]*)",
                                                  RegexOptions.IgnoreCase))
                    local.Add(c.Groups[1].Value);

                foreach (Match m in Regex.Matches(sql,
                             @"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_][a-z0-9_]*)",
                             RegexOptions.IgnoreCase))
                {
                    var t = m.Groups[1].Value;
                    // Keywords that legitimately follow these words: DO UPDATE
                    // SET, INSERT INTO ... SELECT, FROM (SELECT ...).
                    if (Keywords.Contains(t)) continue;
                    if (local.Contains(t)) continue;
                    if (declared.Contains(t)) continue;
                    missing.Add($"{Path.GetFileName(f)} -> {t}");
                }
            }
        }

        Assert.True(missing.Count == 0,
            "endpoint SQL references table(s) no migration creates:\n  "
            + string.Join("\n  ", missing.Distinct()));
    }
}
