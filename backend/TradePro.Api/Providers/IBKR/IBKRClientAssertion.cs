using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;

namespace TradePro.Api.Providers.IBKR;

/// <summary>
/// Pure, dependency-free builder for the IBKR OAuth2 CLIENT-ASSERTION JWT.
///
/// Step 1 of the IBKR OAuth2 flow authenticates the token request with a
/// JWT signed (RS256) by the client's RSA private key instead of a shared
/// secret. IBKR support specified:
///   - header: alg=RS256, typ=JWT, kid={ClientKeyId}
///   - claims: iss=sub={ClientId}, aud={token endpoint}, iat/exp,
///             a random jti, and scope=sso-sessions.write.
///
/// Built by hand (Base64Url(header) . Base64Url(claims) . Base64Url(sig))
/// so it has ZERO package dependency and is trivially unit-testable: a
/// test generates an in-memory RSA key, signs a token, and verifies the
/// signature + claims against the matching public key — no secrets, no
/// network. The signing primitive is <see cref="RSA.SignData(byte[],
/// HashAlgorithmName, RSASignaturePadding)"/> with SHA-256 + PKCS#1 v1.5,
/// which is exactly RS256.
/// </summary>
public static class IBKRClientAssertion
{
    /// <summary>The OAuth2 scope IBKR support specified for the
    /// sso-sessions token request.</summary>
    public const string SsoSessionsWriteScope = "sso-sessions.write";

    /// <summary>
    /// Build + sign the client-assertion JWT.
    /// </summary>
    /// <param name="rsa">RSA key holding the PRIVATE key (signing).</param>
    /// <param name="clientId">OAuth2 client_id → iss + sub.</param>
    /// <param name="clientKeyId">→ JWT header kid.</param>
    /// <param name="audience">token endpoint URL → aud.</param>
    /// <param name="scope">OAuth2 scope claim.</param>
    /// <param name="now">issued-at instant (UTC). Injected for testability.</param>
    /// <param name="lifetime">token validity window. IBKR assertions are
    /// short-lived; default 5 min.</param>
    /// <param name="certificatePem">OPTIONAL PEM X.509 cert. When supplied
    /// (and <paramref name="includeX5c"/> is true) the cert's base64-DER is
    /// added as a single-entry <c>x5c</c> header alongside <c>kid</c>. The
    /// kid stays the default selector (IBKR private_key_jwt). Parsed
    /// defensively: an absent / malformed cert is silently skipped (kid-only
    /// JWT) — it never throws, so a bad cert can't break auth.</param>
    /// <param name="includeX5c">Gate for the x5c header (maps to
    /// <see cref="IBKROptions.UseX5c"/>). Default false → kid-only.</param>
    public static string Build(
        RSA rsa,
        string clientId,
        string clientKeyId,
        string audience,
        string scope = SsoSessionsWriteScope,
        DateTimeOffset? now = null,
        TimeSpan? lifetime = null,
        string? certificatePem = null,
        bool includeX5c = false)
    {
        if (rsa is null) throw new ArgumentNullException(nameof(rsa));
        if (string.IsNullOrWhiteSpace(clientId)) throw new ArgumentException("clientId required", nameof(clientId));
        if (string.IsNullOrWhiteSpace(clientKeyId)) throw new ArgumentException("clientKeyId required", nameof(clientKeyId));

        var issuedAt = now ?? DateTimeOffset.UtcNow;
        var exp = issuedAt.Add(lifetime ?? TimeSpan.FromMinutes(5));

        var header = new Dictionary<string, object>
        {
            ["alg"] = "RS256",
            ["typ"] = "JWT",
            ["kid"] = clientKeyId,
        };

        // Optional x5c: only when explicitly requested AND the cert parses.
        // RFC 7515 x5c is an array of base64 (NOT base64url) DER certs,
        // leaf-first. We add a single entry. Defensive: a missing/garbage
        // cert degrades to kid-only rather than throwing.
        if (includeX5c)
        {
            var der = TryGetCertificateDerBase64(certificatePem);
            if (der is not null)
                header["x5c"] = new[] { der };
        }

        var claims = new Dictionary<string, object>
        {
            ["iss"] = clientId,
            ["sub"] = clientId,
            ["aud"] = audience,
            ["iat"] = issuedAt.ToUnixTimeSeconds(),
            ["exp"] = exp.ToUnixTimeSeconds(),
            ["jti"] = Guid.NewGuid().ToString("N"),
            ["scope"] = scope,
        };

        var headerSeg = Base64UrlEncode(JsonSerializer.SerializeToUtf8Bytes(header));
        var claimsSeg = Base64UrlEncode(JsonSerializer.SerializeToUtf8Bytes(claims));
        var signingInput = $"{headerSeg}.{claimsSeg}";

        var signature = rsa.SignData(
            Encoding.ASCII.GetBytes(signingInput),
            HashAlgorithmName.SHA256,
            RSASignaturePadding.Pkcs1);
        var sigSeg = Base64UrlEncode(signature);

        return $"{signingInput}.{sigSeg}";
    }

    /// <summary>
    /// Load an RSA key from a PEM string (PKCS#8, PKCS#1, or SPKI public).
    /// Tries each known PEM label so the secret can hold any standard
    /// export. Used by the client to materialise <c>IBKR:PrivateKey</c>.
    /// </summary>
    public static RSA ImportPem(string pem)
    {
        if (string.IsNullOrWhiteSpace(pem))
            throw new ArgumentException("PEM is empty", nameof(pem));
        var rsa = RSA.Create();
        // ImportFromPem handles -----BEGIN PRIVATE KEY----- (PKCS#8),
        // -----BEGIN RSA PRIVATE KEY----- (PKCS#1), and the public
        // variants transparently in .NET.
        rsa.ImportFromPem(pem);
        return rsa;
    }

    /// <summary>
    /// Parse a PEM X.509 cert and return its DER as STANDARD base64 (the
    /// encoding RFC 7515 x5c requires — NOT base64url). Returns null on any
    /// problem (empty, not a cert, malformed PEM) so the caller falls back
    /// to a kid-only header. Never throws.
    /// </summary>
    public static string? TryGetCertificateDerBase64(string? pem)
    {
        if (string.IsNullOrWhiteSpace(pem)) return null;
        try
        {
            using var cert = X509Certificate2.CreateFromPem(pem);
            return Convert.ToBase64String(cert.RawData);
        }
        catch
        {
            // Malformed / non-cert PEM → skip x5c, keep kid-only. Auth must
            // not break because the optional cert is bad.
            return null;
        }
    }

    // RFC 7515 Base64Url: '+' → '-', '/' → '_', strip '=' padding.
    private static string Base64UrlEncode(byte[] bytes) =>
        Convert.ToBase64String(bytes)
            .TrimEnd('=')
            .Replace('+', '-')
            .Replace('/', '_');
}
