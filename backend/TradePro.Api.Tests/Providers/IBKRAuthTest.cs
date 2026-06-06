using System.Security.Cryptography;
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
            Mode = "paper", ClientId = "c", ClientKeyId = "k", Credential = "u"
            // missing PrivateKey
        }.IsEnabled);
        Assert.True(new IBKROptions
        {
            Mode = "paper", ClientId = "c", ClientKeyId = "k",
            Credential = "u", PrivateKey = "pem"
        }.IsEnabled);
    }

    private static byte[] B64UrlDecode(string s)
    {
        var t = s.Replace('-', '+').Replace('_', '/');
        switch (t.Length % 4) { case 2: t += "=="; break; case 3: t += "="; break; }
        return Convert.FromBase64String(t);
    }
}
