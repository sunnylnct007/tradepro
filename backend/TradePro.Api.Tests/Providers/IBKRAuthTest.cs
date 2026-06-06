using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// Pure-logic coverage for the IBKR OAuth2 integration — NO secrets, NO
/// network. A test RSA key is generated in-memory; the client-assertion
/// JWT is signed with the private half and verified against the public
/// half. Response parsing is exercised against the documented cpapi JSON
/// shapes. Mode→accountId selection is checked against the spec
/// (paper=DUP6..., live=U2512...).
/// </summary>
public class IBKRAuthTest
{
    private const string Aud = "https://api.ibkr.com/oauth2/api/v1/token";

    // ─── Client-assertion JWT ───────────────────────────────────────

    [Fact]
    public void ClientAssertion_has_RS256_header_with_kid()
    {
        using var rsa = RSA.Create(2048);
        var jwt = IBKRClientAssertion.Build(rsa, "client-123", "key-abc", Aud);

        var parts = jwt.Split('.');
        Assert.Equal(3, parts.Length);

        using var headerDoc = JsonDocument.Parse(B64UrlDecode(parts[0]));
        var h = headerDoc.RootElement;
        Assert.Equal("RS256", h.GetProperty("alg").GetString());
        Assert.Equal("JWT", h.GetProperty("typ").GetString());
        Assert.Equal("key-abc", h.GetProperty("kid").GetString());
    }

    [Fact]
    public void ClientAssertion_claims_carry_clientId_and_scope()
    {
        using var rsa = RSA.Create(2048);
        var now = DateTimeOffset.FromUnixTimeSeconds(1_700_000_000);
        var jwt = IBKRClientAssertion.Build(
            rsa, "client-123", "key-abc", Aud, now: now, lifetime: TimeSpan.FromMinutes(5));

        var parts = jwt.Split('.');
        using var claimsDoc = JsonDocument.Parse(B64UrlDecode(parts[1]));
        var c = claimsDoc.RootElement;
        Assert.Equal("client-123", c.GetProperty("iss").GetString());
        Assert.Equal("client-123", c.GetProperty("sub").GetString());
        Assert.Equal(Aud, c.GetProperty("aud").GetString());
        Assert.Equal("sso-sessions.write", c.GetProperty("scope").GetString());
        Assert.Equal(1_700_000_000, c.GetProperty("iat").GetInt64());
        Assert.Equal(1_700_000_300, c.GetProperty("exp").GetInt64());
        Assert.False(string.IsNullOrEmpty(c.GetProperty("jti").GetString()));
    }

    [Fact]
    public void ClientAssertion_signature_verifies_against_public_key()
    {
        using var rsa = RSA.Create(2048);
        var jwt = IBKRClientAssertion.Build(rsa, "client-123", "key-abc", Aud);
        var parts = jwt.Split('.');

        var signingInput = Encoding.ASCII.GetBytes($"{parts[0]}.{parts[1]}");
        var signature = B64UrlDecode(parts[2]);

        // Verify with a SEPARATE RSA instance holding only the public key —
        // proves the signature is a real RS256 signature over the segments.
        using var verifier = RSA.Create();
        verifier.ImportRSAPublicKey(rsa.ExportRSAPublicKey(), out _);
        var ok = verifier.VerifyData(
            signingInput, signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        Assert.True(ok);
    }

    [Fact]
    public void ClientAssertion_signature_rejects_tampered_payload()
    {
        using var rsa = RSA.Create(2048);
        var jwt = IBKRClientAssertion.Build(rsa, "client-123", "key-abc", Aud);
        var parts = jwt.Split('.');

        // Flip a byte in the claims segment → signature must no longer verify.
        var tampered = Encoding.ASCII.GetBytes($"{parts[0]}.{parts[1]}X");
        var signature = B64UrlDecode(parts[2]);
        var ok = rsa.VerifyData(
            tampered, signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        Assert.False(ok);
    }

    [Fact]
    public void ImportPem_roundtrips_a_pkcs8_private_key()
    {
        using var original = RSA.Create(2048);
        var pem = original.ExportPkcs8PrivateKeyPem();

        using var loaded = IBKRClientAssertion.ImportPem(pem);
        // Sign with the loaded key, verify with the original's public half.
        var jwt = IBKRClientAssertion.Build(loaded, "c", "k", Aud);
        var parts = jwt.Split('.');
        var ok = original.VerifyData(
            Encoding.ASCII.GetBytes($"{parts[0]}.{parts[1]}"),
            B64UrlDecode(parts[2]),
            HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        Assert.True(ok);
    }

    // ─── x5c certificate header (optional, config-flippable) ────────

    [Fact]
    public void ClientAssertion_kid_mode_has_no_x5c_header()
    {
        // Default (includeX5c=false): kid-only, never an x5c — even when a
        // perfectly good cert is handed in.
        using var rsa = RSA.Create(2048);
        var certPem = SelfSignedCertPem(rsa);
        var jwt = IBKRClientAssertion.Build(
            rsa, "client-123", "key-abc", Aud, certificatePem: certPem, includeX5c: false);

        using var headerDoc = JsonDocument.Parse(B64UrlDecode(jwt.Split('.')[0]));
        var h = headerDoc.RootElement;
        Assert.Equal("key-abc", h.GetProperty("kid").GetString());
        Assert.False(h.TryGetProperty("x5c", out _), "kid-mode JWT must not carry x5c");
    }

    [Fact]
    public void ClientAssertion_useX5c_emits_base64_der_matching_the_cert()
    {
        using var rsa = RSA.Create(2048);
        var certPem = SelfSignedCertPem(rsa);
        var jwt = IBKRClientAssertion.Build(
            rsa, "client-123", "key-abc", Aud, certificatePem: certPem, includeX5c: true);

        using var headerDoc = JsonDocument.Parse(B64UrlDecode(jwt.Split('.')[0]));
        var h = headerDoc.RootElement;
        // kid is still present — x5c is ADDITIVE, not a replacement.
        Assert.Equal("key-abc", h.GetProperty("kid").GetString());

        // x5c is a JSON array of base64 (standard, NOT base64url) DER certs.
        Assert.True(h.TryGetProperty("x5c", out var x5c));
        Assert.Equal(JsonValueKind.Array, x5c.ValueKind);
        Assert.Equal(1, x5c.GetArrayLength());
        var entry = x5c[0].GetString()!;

        // The entry must round-trip to the SAME cert DER — proves it's the
        // real cert, base64-encoded, not garbage.
        using var fromHeader = new X509Certificate2(Convert.FromBase64String(entry));
        using var original = X509Certificate2.CreateFromPem(certPem);
        Assert.Equal(original.RawData, fromHeader.RawData);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("-----BEGIN CERTIFICATE-----\nnot a real cert\n-----END CERTIFICATE-----")]
    [InlineData("total garbage, definitely not PEM")]
    public void ClientAssertion_useX5c_with_absent_or_malformed_cert_degrades_to_kid_only(string? badCert)
    {
        // UseX5c=true but the cert is missing/broken → no throw, kid-only JWT.
        using var rsa = RSA.Create(2048);
        var jwt = IBKRClientAssertion.Build(
            rsa, "client-123", "key-abc", Aud, certificatePem: badCert, includeX5c: true);

        var parts = jwt.Split('.');
        Assert.Equal(3, parts.Length);
        using var headerDoc = JsonDocument.Parse(B64UrlDecode(parts[0]));
        var h = headerDoc.RootElement;
        Assert.Equal("key-abc", h.GetProperty("kid").GetString());
        Assert.False(h.TryGetProperty("x5c", out _),
            "malformed/absent cert with UseX5c must not produce an x5c entry");

        // And the JWT is still a valid RS256 signature (auth not broken).
        var ok = rsa.VerifyData(
            Encoding.ASCII.GetBytes($"{parts[0]}.{parts[1]}"),
            B64UrlDecode(parts[2]),
            HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        Assert.True(ok);
    }

    [Fact]
    public void TryGetCertificateDerBase64_returns_null_for_garbage_and_der_for_valid()
    {
        Assert.Null(IBKRClientAssertion.TryGetCertificateDerBase64(null));
        Assert.Null(IBKRClientAssertion.TryGetCertificateDerBase64(""));
        Assert.Null(IBKRClientAssertion.TryGetCertificateDerBase64("nope"));

        using var rsa = RSA.Create(2048);
        var certPem = SelfSignedCertPem(rsa);
        var der = IBKRClientAssertion.TryGetCertificateDerBase64(certPem);
        Assert.NotNull(der);
        using var cert = X509Certificate2.CreateFromPem(certPem);
        Assert.Equal(Convert.ToBase64String(cert.RawData), der);
    }

    // ─── Response parsing ───────────────────────────────────────────

    [Fact]
    public void ParseToken_reads_access_token_and_expiry()
    {
        var json = """
        { "access_token": "tok-xyz", "token_type": "Bearer",
          "expires_in": 86400, "scope": "sso-sessions.write" }
        """;
        var t = IBKRResponseParser.ParseToken(json);
        Assert.Equal("tok-xyz", t.AccessToken);
        Assert.Equal("Bearer", t.TokenType);
        Assert.Equal(86400, t.ExpiresInSeconds);
        Assert.Equal("sso-sessions.write", t.Scope);
    }

    [Fact]
    public void ParseSsoSession_accepts_session_token_or_access_token()
    {
        Assert.Equal("sess-1",
            IBKRResponseParser.ParseSsoSession("""{ "session_token": "sess-1" }""").SessionToken);
        Assert.Equal("acc-1",
            IBKRResponseParser.ParseSsoSession("""{ "access_token": "acc-1" }""").SessionToken);
    }

    [Fact]
    public void ParseTickle_reads_session_and_auth_status()
    {
        var json = """
        { "session": "abc123",
          "iserver": { "authStatus": { "authenticated": true, "connected": true } } }
        """;
        var t = IBKRResponseParser.ParseTickle(json);
        Assert.Equal("abc123", t.Session);
        Assert.True(t.Authenticated);
        Assert.True(t.Connected);
    }

    [Fact]
    public void ParseAccounts_reads_account_id_array()
    {
        var json = """{ "accounts": ["DUP600001", "U2512345"], "selectedAccount": "DUP600001" }""";
        var ids = IBKRResponseParser.ParseAccounts(json);
        Assert.Equal(new[] { "DUP600001", "U2512345" }, ids);
    }

    [Fact]
    public void ParsePositions_maps_neutral_shape()
    {
        var json = """
        [ { "conid": 265598, "ticker": "AAPL", "position": 10,
            "avgCost": 150.5, "mktPrice": 172.0, "mktValue": 1720.0,
            "unrealizedPnl": 215.0, "currency": "USD" } ]
        """;
        var positions = IBKRResponseParser.ParsePositions(json);
        var p = Assert.Single(positions);
        Assert.Equal(265598, p.ConId);
        Assert.Equal("AAPL", p.Symbol);
        Assert.Equal(10m, p.Quantity);
        Assert.Equal(150.5m, p.AvgCost);
        Assert.Equal(172.0m, p.MarketPrice);
        Assert.Equal(215.0m, p.UnrealizedPnl);
        Assert.Equal("USD", p.Currency);
    }

    [Fact]
    public void ParseLedger_prefers_BASE_row()
    {
        var json = """
        { "USD": { "cashbalance": 1000, "netliquidationvalue": 5000, "currency": "USD" },
          "BASE": { "cashbalance": 9000, "netliquidationvalue": 42000,
                    "unrealizedpnl": 123.4, "currency": "USD" } }
        """;
        var c = IBKRResponseParser.ParseLedger(json);
        Assert.Equal(9000m, c.Cash);
        Assert.Equal(42000m, c.NetLiquidation);
        Assert.Equal(123.4m, c.UnrealizedPnl);
        Assert.Equal("USD", c.Currency);
    }

    [Fact]
    public void ParseOrderAck_reads_placed_order_id()
    {
        var json = """[ { "order_id": "987654", "order_status": "Submitted" } ]""";
        var ack = IBKRResponseParser.ParseOrderAck(json);
        Assert.Equal("987654", ack.OrderId);
        Assert.Equal("Submitted", ack.OrderStatus);
        Assert.Null(ack.Error);
    }

    [Fact]
    public void ParseOrderAck_detects_confirmation_reply()
    {
        var json = """[ { "id": "reply-42", "message": ["Confirm order price?"] } ]""";
        var ack = IBKRResponseParser.ParseOrderAck(json);
        Assert.Null(ack.OrderId);
        Assert.Equal("reply-42", ack.ReplyId);
        Assert.Equal("NEEDS_CONFIRM", ack.OrderStatus);
    }

    [Fact]
    public void ParseOrderAck_surfaces_error()
    {
        var ack = IBKRResponseParser.ParseOrderAck("""{ "error": "insufficient funds" }""");
        Assert.Equal("insufficient funds", ack.Error);
        Assert.Null(ack.OrderId);
    }

    // ─── Mode → account / label selection ───────────────────────────

    [Fact]
    public void Mode_paper_selects_paper_account_and_label()
    {
        var o = new IBKROptions { Mode = "paper", AccountIdPaper = "DUP600001", AccountIdLive = "U2512345" };
        Assert.Equal("DUP600001", o.AccountId);
        Assert.Equal("IBKR_PAPER", o.BrokerLabel);
    }

    [Fact]
    public void Mode_live_selects_live_account_and_label()
    {
        var o = new IBKROptions { Mode = "live", AccountIdPaper = "DUP600001", AccountIdLive = "U2512345" };
        Assert.Equal("U2512345", o.AccountId);
        Assert.Equal("IBKR_LIVE", o.BrokerLabel);
    }

    [Fact]
    public void IsEnabled_false_until_mode_and_signing_material_present()
    {
        Assert.False(new IBKROptions().IsEnabled); // disabled default
        Assert.False(new IBKROptions
        {
            Mode = "paper", ClientIdPaper = "c", ClientKeyId = "k",
            CredentialPaper = "u", AccountIdPaper = "DUP6"
            // missing PrivateKey
        }.IsEnabled);
        Assert.False(new IBKROptions
        {
            // missing the active (paper) account id
            Mode = "paper", ClientIdPaper = "c", ClientKeyId = "k",
            CredentialPaper = "u", PrivateKey = "pem"
        }.IsEnabled);
        Assert.True(new IBKROptions
        {
            Mode = "paper", ClientIdPaper = "c", ClientKeyId = "k",
            CredentialPaper = "u", PrivateKey = "pem", AccountIdPaper = "DUP6"
        }.IsEnabled);
        // Backward-compat: legacy single ClientId/Credential still enables.
        Assert.True(new IBKROptions
        {
            Mode = "paper", ClientId = "c", ClientKeyId = "k",
            Credential = "u", PrivateKey = "pem", AccountIdPaper = "DUP6"
        }.IsEnabled);
    }

    // ─── Per-env (paper|live) credential resolution ─────────────────

    [Fact]
    public void Mode_paper_resolves_paper_clientId_credential_account()
    {
        var o = new IBKROptions
        {
            Mode = "paper",
            ClientIdPaper = "cid-paper",   ClientIdLive = "cid-live",
            CredentialPaper = "user-paper", CredentialLive = "user-live",
            AccountIdPaper = "DUP600001",   AccountIdLive = "U2512345",
        };
        Assert.Equal("cid-paper", o.ActiveClientId);
        Assert.Equal("user-paper", o.ActiveCredential);
        Assert.Equal("DUP600001", o.ActiveAccountId);
        Assert.Equal("DUP600001", o.AccountId);
        Assert.Equal("IBKR_PAPER", o.BrokerLabel);
    }

    [Fact]
    public void Mode_live_resolves_live_clientId_credential_account()
    {
        var o = new IBKROptions
        {
            Mode = "live",
            ClientIdPaper = "cid-paper",   ClientIdLive = "cid-live",
            CredentialPaper = "user-paper", CredentialLive = "user-live",
            AccountIdPaper = "DUP600001",   AccountIdLive = "U2512345",
        };
        Assert.Equal("cid-live", o.ActiveClientId);
        Assert.Equal("user-live", o.ActiveCredential);
        Assert.Equal("U2512345", o.ActiveAccountId);
        Assert.Equal("U2512345", o.AccountId);
        Assert.Equal("IBKR_LIVE", o.BrokerLabel);
    }

    [Fact]
    public void OldSingleKey_fallback_when_perEnv_absent()
    {
        // Only the legacy single ClientId/Credential are set — the Active*
        // resolvers must fall back to them for BOTH modes.
        var paper = new IBKROptions
        {
            Mode = "paper", ClientId = "legacy-cid", Credential = "legacy-user",
            AccountIdPaper = "DUP6",
        };
        Assert.Equal("legacy-cid", paper.ActiveClientId);
        Assert.Equal("legacy-user", paper.ActiveCredential);

        var live = new IBKROptions
        {
            Mode = "live", ClientId = "legacy-cid", Credential = "legacy-user",
            AccountIdLive = "U25",
        };
        Assert.Equal("legacy-cid", live.ActiveClientId);
        Assert.Equal("legacy-user", live.ActiveCredential);
    }

    [Fact]
    public void PerEnv_wins_over_legacy_single_key_when_both_present()
    {
        var o = new IBKROptions
        {
            Mode = "live",
            ClientId = "legacy-cid",   Credential = "legacy-user",
            ClientIdLive = "live-cid", CredentialLive = "live-user",
            AccountIdLive = "U25",
        };
        Assert.Equal("live-cid", o.ActiveClientId);
        Assert.Equal("live-user", o.ActiveCredential);
    }

    [Fact]
    public void ClientAssertion_carries_the_ACTIVE_clientId_as_iss_sub()
    {
        // The JWT iss/sub must be the active (mode-resolved) client id, not
        // the legacy single one — paper and live sign with different iss.
        var paper = new IBKROptions
        {
            Mode = "paper", ClientIdPaper = "cid-paper", ClientIdLive = "cid-live",
        };
        var live = new IBKROptions
        {
            Mode = "live", ClientIdPaper = "cid-paper", ClientIdLive = "cid-live",
        };

        using var rsa = RSA.Create(2048);
        var jwtPaper = IBKRClientAssertion.Build(rsa, paper.ActiveClientId, "kid", Aud);
        var jwtLive = IBKRClientAssertion.Build(rsa, live.ActiveClientId, "kid", Aud);

        using var pDoc = JsonDocument.Parse(B64UrlDecode(jwtPaper.Split('.')[1]));
        Assert.Equal("cid-paper", pDoc.RootElement.GetProperty("iss").GetString());
        Assert.Equal("cid-paper", pDoc.RootElement.GetProperty("sub").GetString());

        using var lDoc = JsonDocument.Parse(B64UrlDecode(jwtLive.Split('.')[1]));
        Assert.Equal("cid-live", lDoc.RootElement.GetProperty("iss").GetString());
        Assert.Equal("cid-live", lDoc.RootElement.GetProperty("sub").GetString());
    }

    // ─── SecretsBundleLoader IBKR key map ───────────────────────────

    [Fact]
    public void SecretsBundleLoader_maps_certificate_to_IBKR_Certificate()
    {
        // The standalone tradepro/ibkr secret carries `certificate` (the
        // PEM IBKR issues); the loader must map it to IBKR:Certificate so it
        // binds onto IBKROptions.Certificate. The map is a private static
        // field on the loader — read it reflectively so the test pins the
        // exact key→config contract without widening the loader's surface.
        var map = (System.Collections.Generic.Dictionary<string, string>)
            typeof(TradePro.Api.Auth.SecretsBundleLoader)
                .GetField("IbkrKeyMap",
                    System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)!
                .GetValue(null)!;

        Assert.True(map.ContainsKey("certificate"), "IBKR key map must carry `certificate`");
        Assert.Equal("IBKR:Certificate", map["certificate"]);

        // Sanity: the shared signing-material keys are still mapped.
        Assert.Equal("IBKR:PrivateKey", map["private_key"]);
        Assert.Equal("IBKR:ClientKeyId", map["client_key_id"]);
        Assert.Equal("IBKR:PublicKey", map["public_key"]);
        Assert.Equal("IBKR:SourceIp", map["ip"]);
        Assert.Equal("IBKR:Mode", map["mode"]);

        // Per-env (paper) keys → Active* resolvers select these in paper mode.
        Assert.Equal("IBKR:ClientIdPaper", map["client_id_paper"]);
        Assert.Equal("IBKR:CredentialPaper", map["credential_paper"]);
        Assert.Equal("IBKR:AccountIdPaper", map["account_id_paper"]);

        // Per-env (live) keys.
        Assert.Equal("IBKR:ClientIdLive", map["client_id_live"]);
        Assert.Equal("IBKR:CredentialLive", map["credential_live"]);
        Assert.Equal("IBKR:AccountIdLive", map["account_id_live"]);

        // Backward-compat legacy single keys remain mapped (fallback).
        Assert.Equal("IBKR:ClientId", map["client_id"]);
        Assert.Equal("IBKR:Credential", map["credential"]);
    }

    [Fact]
    public void IBKROptions_certificate_defaults_empty_and_useX5c_false()
    {
        var o = new IBKROptions();
        Assert.Equal(string.Empty, o.Certificate);
        Assert.False(o.UseX5c);
    }

    private static byte[] B64UrlDecode(string s)
    {
        var t = s.Replace('-', '+').Replace('_', '/');
        switch (t.Length % 4) { case 2: t += "=="; break; case 3: t += "="; break; }
        return Convert.FromBase64String(t);
    }

    /// <summary>Generate a self-signed X.509 cert (PEM) bound to the given
    /// RSA key — in-memory, no files, no network. Used to exercise the x5c
    /// path with a real, parseable certificate.</summary>
    private static string SelfSignedCertPem(RSA rsa)
    {
        var req = new CertificateRequest(
            "CN=tradepro-ibkr-test", rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        using var cert = req.CreateSelfSigned(
            DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddYears(1));
        return new string(PemEncoding.Write("CERTIFICATE", cert.RawData));
    }
}
