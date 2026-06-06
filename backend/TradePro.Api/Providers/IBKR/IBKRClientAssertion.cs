using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;

namespace TradePro.Api.Providers.IBKR;

/// <summary>
/// Pure, dependency-free builder for the IBKR OAuth2 signed JWTs.
///
/// SOURCE OF TRUTH for every claim set below: IBKR's official Postman
/// collection "IB Public APIs (Trading API)" — the requests are ported
/// VERBATIM (no paraphrase). Two distinct JWTs are produced:
///
///   1. <see cref="Build"/> — the STEP-1 CLIENT-ASSERTION JWT that
///      authenticates POST {oauth2Url}/api/v1/token. Per the collection's
///      "Create OAuth 2.0 Access Token" pre-request script:
///        - header: { "typ":"JWT", "alg":"RS256", "kid": clientKeyId }
///        - claims: { "iss": clientId, "sub": clientId, "aud": "/token",
///                    "exp": now+20, "iat": now-10 }
///        ── NOTE aud is the LITERAL string "/token" (NOT the full URL),
///           and there is NO jti / NO scope claim in the JWT (scope rides
///           in the form-urlencoded body, not the assertion).
///
///   2. <see cref="BuildSsoSession"/> — the STEP-2 SSO-SESSION JWT that is
///      itself the BODY of POST {gatewayUrl}/api/v1/sso-sessions (sent with
///      Content-Type: application/jwt, NOT JSON). Per the collection's
///      "Create SSO Session" pre-request script:
///        - header: { "typ":"JWT", "alg":"RS256", "kid": clientKeyId }
///        - claims: { "ip": egressIp, "credential": username,
///                    "iss": clientId, "exp": now+86400, "iat": now }
///        ── NOTE: NO aud and NO sub claim here (differs from step 1).
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
    /// sso-sessions token request. Sent in the STEP-1 form-urlencoded body
    /// (NOT inside the client-assertion JWT — per the Postman collection).</summary>
    public const string SsoSessionsWriteScope = "sso-sessions.write";

    /// <summary>STEP-1 literal aud claim from the Postman collection — the
    /// client-assertion JWT's <c>aud</c> is the constant string "/token",
    /// not the token endpoint URL.</summary>
    public const string TokenAudience = "/token";

    /// <summary>
    /// STEP 1 — build + sign the CLIENT-ASSERTION JWT for
    /// POST {oauth2Url}/api/v1/token.
    ///
    /// VERBATIM from the IBKR Postman collection ("Create OAuth 2.0 Access
    /// Token" pre-request script):
    ///   header: { "typ":"JWT", "alg":"RS256", "kid": clientKeyId }
    ///   claims: { "iss": clientId, "sub": clientId, "aud": "/token",
    ///             "exp": now+20, "iat": now-10 }
    /// No jti, no scope in the JWT — scope rides in the form body.
    /// </summary>
    /// <param name="rsa">RSA key holding the PRIVATE key (signing).</param>
    /// <param name="clientId">OAuth2 client_id → iss + sub.</param>
    /// <param name="clientKeyId">→ JWT header kid.</param>
    /// <param name="audience">aud claim. Defaults to the collection's literal
    /// "/token" (<see cref="TokenAudience"/>); overridable for tests.</param>
    /// <param name="now">"now" instant (UTC). Injected for testability. The
    /// collection sets iat = now-10 and exp = now+20 around this instant.</param>
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
        string audience = TokenAudience,
        DateTimeOffset? now = null,
        string? certificatePem = null,
        bool includeX5c = false)
    {
        if (rsa is null) throw new ArgumentNullException(nameof(rsa));
        if (string.IsNullOrWhiteSpace(clientId)) throw new ArgumentException("clientId required", nameof(clientId));
        if (string.IsNullOrWhiteSpace(clientKeyId)) throw new ArgumentException("clientKeyId required", nameof(clientKeyId));

        var nowInstant = now ?? DateTimeOffset.UtcNow;
        // Postman collection: exp = now+20s, iat = now-10s (small skew window).
        var iat = nowInstant.AddSeconds(-10).ToUnixTimeSeconds();
        var exp = nowInstant.AddSeconds(20).ToUnixTimeSeconds();

        var header = BuildHeader(clientKeyId, certificatePem, includeX5c);

        // Claim ORDER + SET are verbatim per the Postman collection.
        var claims = new Dictionary<string, object>
        {
            ["iss"] = clientId,
            ["sub"] = clientId,
            ["aud"] = audience,
            ["exp"] = exp,
            ["iat"] = iat,
        };

        return Sign(rsa, header, claims);
    }

    /// <summary>
    /// STEP 2 — build + sign the SSO-SESSION JWT that is itself the request
    /// BODY of POST {gatewayUrl}/api/v1/sso-sessions (Content-Type:
    /// application/jwt). THIS is the fix for the
    /// <c>400 "Invalid payload for security policy: SIGNED_JWT"</c> error:
    /// IBKR's gateway requires a signed JWT here, not a JSON body.
    ///
    /// VERBATIM from the IBKR Postman collection ("Create SSO Session"
    /// pre-request script):
    ///   header: { "typ":"JWT", "alg":"RS256", "kid": clientKeyId }
    ///   claims: { "ip": ip, "credential": credential, "iss": clientId,
    ///             "exp": now+86400, "iat": now }
    /// IMPORTANT: NO aud, NO sub claim (differs from step 1). The optional
    /// alternativeIps array is omitted.
    /// </summary>
    /// <param name="rsa">RSA key holding the PRIVATE key (signing) — the SAME
    /// signing pair / kid as the step-1 assertion.</param>
    /// <param name="clientId">OAuth2 client_id → iss.</param>
    /// <param name="clientKeyId">→ JWT header kid.</param>
    /// <param name="credential">IBKR brokerage username → credential claim.</param>
    /// <param name="ip">Resolved egress IP → ip claim.</param>
    /// <param name="now">"now" instant (UTC). Injected for testability:
    /// iat = now, exp = now+86400 (24h).</param>
    public static string BuildSsoSession(
        RSA rsa,
        string clientId,
        string clientKeyId,
        string credential,
        string ip,
        DateTimeOffset? now = null)
    {
        if (rsa is null) throw new ArgumentNullException(nameof(rsa));
        if (string.IsNullOrWhiteSpace(clientId)) throw new ArgumentException("clientId required", nameof(clientId));
        if (string.IsNullOrWhiteSpace(clientKeyId)) throw new ArgumentException("clientKeyId required", nameof(clientKeyId));
        if (string.IsNullOrWhiteSpace(credential)) throw new ArgumentException("credential required", nameof(credential));
        if (string.IsNullOrWhiteSpace(ip)) throw new ArgumentException("ip required", nameof(ip));

        var nowInstant = now ?? DateTimeOffset.UtcNow;
        // Postman collection: exp = now+86400s (24h), iat = now.
        var iat = nowInstant.ToUnixTimeSeconds();
        var exp = nowInstant.AddSeconds(86400).ToUnixTimeSeconds();

        // step 2 reuses the SAME header shape (typ/alg/kid) as the assertion.
        var header = BuildHeader(clientKeyId, certificatePem: null, includeX5c: false);

        // Claim SET is verbatim per the Postman collection — note NO aud, NO sub.
        var claims = new Dictionary<string, object>
        {
            ["ip"] = ip,
            ["credential"] = credential,
            ["iss"] = clientId,
            ["exp"] = exp,
            ["iat"] = iat,
        };

        return Sign(rsa, header, claims);
    }

    /// <summary>Shared header builder: { typ, alg, kid } with the optional,
    /// config-gated x5c entry (kid-only by default).</summary>
    private static Dictionary<string, object> BuildHeader(
        string clientKeyId, string? certificatePem, bool includeX5c)
    {
        var header = new Dictionary<string, object>
        {
            ["typ"] = "JWT",
            ["alg"] = "RS256",
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
        return header;
    }

    /// <summary>RS256-sign the header + claims into a compact JWS
    /// (Base64Url(header) . Base64Url(claims) . Base64Url(sig)).</summary>
    private static string Sign(
        RSA rsa, Dictionary<string, object> header, Dictionary<string, object> claims)
    {
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
