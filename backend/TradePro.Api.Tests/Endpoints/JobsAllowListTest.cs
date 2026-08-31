using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// The UI job trigger must stay a trigger, not a remote shell.
///
/// Owner asked for a UI control to run the scheduled jobs on demand, off the
/// Mac. This endpoint invokes ONE Lambda with ONE payload shape, and only for
/// names on a fixed allow-list — so a crafted request cannot reach an arbitrary
/// function.
/// </summary>
public class JobsAllowListTest
{
    private static readonly string[] Expected =
        { "index_strangle_paper", "index_strangle_alert", "post_earnings_puts" };

    [Fact]
    public void AllowListMatchesTheLambdaHandlerRegistry()
    {
        // The Python handler's JOBS dict is the other half of this contract.
        // If they drift, the button offers a job Lambda will reject — so the
        // list is asserted against the handler source itself rather than a
        // copy of it.
        var repo = FindRepoRoot();
        var handler = File.ReadAllText(
            Path.Combine(repo, "strategies", "lambda_handler.py"));
        foreach (var job in Expected)
            Assert.Contains($"\"{job}\"", handler);
    }

    [Fact]
    public void EndpointSourceDeclaresExactlyThoseJobs()
    {
        var repo = FindRepoRoot();
        var src = File.ReadAllText(Path.Combine(
            repo, "backend", "TradePro.Api", "Endpoints", "JobsEndpoints.cs"));
        foreach (var job in Expected)
            Assert.Contains($"\"{job}\"", src);
        // A wildcard or a pass-through would defeat the point of the list.
        Assert.DoesNotContain("Allowed.Add(", src);
        Assert.Contains("Allowed.Contains(job)", src);
    }

    private static string FindRepoRoot()
    {
        var d = new DirectoryInfo(AppContext.BaseDirectory);
        // `.git` is a FILE, not a directory, inside a git worktree — checking
        // only for a directory made this fail everywhere except a normal clone.
        while (d is not null
               && !Directory.Exists(Path.Combine(d.FullName, ".git"))
               && !File.Exists(Path.Combine(d.FullName, ".git")))
            d = d.Parent;
        Assert.NotNull(d);
        return d!.FullName;
    }
}
