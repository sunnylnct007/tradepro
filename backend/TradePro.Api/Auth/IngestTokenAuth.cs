using System.Security.Claims;
using System.Text.Encodings.Web;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authorization;
using Microsoft.Extensions.Options;

namespace TradePro.Api.Auth;

/// Static-token Bearer auth used by /api/ingest/* — separate from Firebase
/// because the Mac that pushes results doesn't sign in with a user. The
/// expected token is configured as `Ingest:Token` (or env var Ingest__Token
/// on App Service) and matched against the `Authorization: Bearer ...`
/// header. There is no claim/whitelist beyond "token matched".
public static class IngestTokenAuth
{
    public const string Scheme = "IngestToken";
    public const string Policy = "IngestClient";

    public static AuthenticationBuilder AddIngestToken(this AuthenticationBuilder builder)
        => builder.AddScheme<IngestTokenOptions, IngestTokenHandler>(Scheme, _ => { });

    public static AuthorizationOptions AddIngestPolicy(this AuthorizationOptions options)
    {
        options.AddPolicy(Policy, p =>
        {
            p.AddAuthenticationSchemes(Scheme);
            p.RequireAuthenticatedUser();
        });
        return options;
    }
}

public class IngestTokenOptions : AuthenticationSchemeOptions { }

public class IngestTokenHandler : AuthenticationHandler<IngestTokenOptions>
{
    private readonly string? _expected;

    public IngestTokenHandler(
        IOptionsMonitor<IngestTokenOptions> options,
        ILoggerFactory logger,
        UrlEncoder encoder,
        IConfiguration config)
        : base(options, logger, encoder)
    {
        _expected = config["Ingest:Token"];
    }

    protected override Task<AuthenticateResult> HandleAuthenticateAsync()
    {
        if (string.IsNullOrEmpty(_expected))
        {
            return Task.FromResult(AuthenticateResult.Fail(
                "Ingest:Token is not configured on the server"));
        }

        if (!Request.Headers.TryGetValue("Authorization", out var header))
        {
            return Task.FromResult(AuthenticateResult.NoResult());
        }

        var raw = header.ToString();
        const string prefix = "Bearer ";
        if (!raw.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        {
            return Task.FromResult(AuthenticateResult.NoResult());
        }

        var token = raw[prefix.Length..].Trim();
        if (!string.Equals(token, _expected, StringComparison.Ordinal))
        {
            return Task.FromResult(AuthenticateResult.Fail("invalid ingest token"));
        }

        var identity = new ClaimsIdentity(
            new[] { new Claim(ClaimTypes.Name, "ingest") },
            Scheme.Name);
        var ticket = new AuthenticationTicket(new ClaimsPrincipal(identity), Scheme.Name);
        return Task.FromResult(AuthenticateResult.Success(ticket));
    }
}
