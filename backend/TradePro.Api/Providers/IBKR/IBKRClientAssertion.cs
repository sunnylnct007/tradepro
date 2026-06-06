using System.Security.Cryptography;
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
    public static string Build(
        RSA rsa,
        string clientId,
        string clientKeyId,
        string audience,
        string scope = SsoSessionsWriteScope,
        DateTimeOffset? now = null,
        TimeSpan? lifetime = null)
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

    // RFC 7515 Base64Url: '+' → '-', '/' → '_', strip '=' padding.
    private static string Base64UrlEncode(byte[] bytes) =>
        Convert.ToBase64String(bytes)
            .TrimEnd('=')
            .Replace('+', '-')
            .Replace('/', '_');
}
