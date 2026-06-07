# IBKR Web API — working notes & handoff (auth + market-data harvest)

**Status:** auth **proven working against live** (2026-06-07). This documents the OAuth2 flow we
reverse-engineered + the non-obvious gotchas, so the **data-framework session can build the IBKR
market-data harvest without re-solving them.** Reference implementation:
`backend/TradePro.Api/Providers/IBKR/IBKRClient.cs` (+ `IBKRClientAssertion.cs`).

> Source of truth for the request shapes: IBKR's official **"IB Public APIs (Trading API)" Postman
> collection** (the `Create OAuth 2.0 Access Token` + `Create SSO Session` pre-request scripts).
> When in doubt, port from that collection **verbatim** — paraphrasing is what cost us a day.

## Base URLs
- `oauth2Url`       = `https://api.ibkr.com/oauth2`
- `gatewayUrl`      = `https://api.ibkr.com/gw`
- `clientPortalUrl` = `https://api.ibkr.com`  (the `/v1/api/...` endpoints)

Paper and live use the **same base URLs** — only the clientId/credential/account differ.

## Required headers on EVERY request (IBKR rejects otherwise)
`Accept: */*` · `Accept-Encoding: gzip, deflate` · `Connection: keep-alive` · `User-Agent: <anything>`
- ⚠️ **GOTCHA — gzip:** because you send `Accept-Encoding: gzip, deflate`, IBKR gzip-compresses
  responses. If your HTTP client doesn't auto-decompress you'll parse the gzip magic byte (`0x1F`)
  as JSON → `"'0x1F' is an invalid start of a value"`. Enable automatic decompression
  (in .NET: `HttpClientHandler.AutomaticDecompression = GZip | Deflate`).

## Auth flow (4 steps)
RSA key pair (PKCS8 private + public). Public key is **registered with IBKR**; you sign JWTs with the
private key. `kid` = your client key id (we use `main`). No certificate, no account password.

### 1. Access token — `POST {oauth2Url}/api/v1/token` (body: x-www-form-urlencoded)
`grant_type=client_credentials`, `scope=sso-sessions.write`,
`client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`,
`client_assertion=<JWT>` where the JWT is:
- header `{ typ:"JWT", alg:"RS256", kid:<keyId> }`
- claims `{ iss:<clientId>, sub:<clientId>, aud:"/token", exp:now+20, iat:now-10 }`
  — ⚠️ **`aud` is the literal string `/token`** (the Postman `serverAudience` var), not a URL.
→ returns `access_token` (call it **TOKEN_ACCESS**).

### 2. SSO session — `POST {gatewayUrl}/api/v1/sso-sessions`
- Headers: `Content-Type: application/jwt`, `Authorization: Bearer {TOKEN_ACCESS}`
- ⚠️ **GOTCHA — the body is a RAW SIGNED JWT (JWS), NOT JSON.** Sending JSON → `400 "Invalid
  payload for security policy: SIGNED_JWT"`. The signed JWT:
  - header `{ typ:"JWT", alg:"RS256", kid:<keyId> }`
  - claims `{ ip:<egress ip>, credential:<IBKR username>, iss:<clientId>, exp:now+86400, iat:now }`
    — **no `aud`, no `sub`** here (differs from step 1).
→ returns its **own** `access_token` (call it **SSO_ACCESS**). **Use SSO_ACCESS — not TOKEN_ACCESS —
  as the Bearer for every `/v1/api/...` call below.**
- ⚠️ **`ip`** must be the **egress IP** of the calling host (what IBKR sees as the source). We
  auto-detect it at runtime (ipify/checkip) so it survives EC2 restarts; the secret `ip` is an
  optional override. A wrong IP → 401.

### 3. Tickle — `POST {clientPortalUrl}/v1/api/tickle` (Bearer SSO_ACCESS) — keepalive.
### 4. Init brokerage session — `POST {clientPortalUrl}/v1/api/iserver/auth/ssodh/init`
body `{ "publish": true, "compete": true }` (Bearer SSO_ACCESS).
⚠️ **Required before any `/iserver/...` call answers reliably** (incl. market data snapshot/history).
Confirm with `GET /v1/api/iserver/accounts`.

## Provisioning gotcha (paper vs live)
IBKR enables OAuth2 clients **per-environment**. As of 2026-06-07 our **live** client authenticates
end-to-end; the **paper** client returns **403 Forbidden** at `sso-sessions` — it is **not yet
provisioned** by IBKR (open onboarding ticket). Same key/signing/IP → a 403 (not 401/400) means
"client recognised but not authorised": chase IBKR provisioning, not the code. (A 401 ⇒ IP/cred; a
400 SIGNED_JWT ⇒ body shape.)

## Cooldown when testing
Our client backs off **60s** after a failed auth and reports `"backing off after a recent failure"`
instead of re-trying — so to see the *real* error, poll `/ibkr/status` slower than once/60s.

## Market-data endpoints (the harvest target) — all Bearer SSO_ACCESS, after ssodh/init
- **Snapshot:** `GET /v1/api/iserver/marketdata/snapshot?conids=<conid>@SMART&fields=31,55,6509,84`
  (last, symbol, etc.).
- **Historical bars:** `GET /v1/api/iserver/marketdata/history?conid=<conid>&exchange=SMART&period=3d&bar=1d&outsideRth=false`
- **Historical bars (beta/HMDS):** `GET /v1/api/hmds/history?conid=<conid>&period=3d&bar=1d&outsideRth=false`
- **Resolve symbol → conid:** `POST /v1/api/iserver/secdef/search` `{ "symbol":"AAPL", "name":true, "secType":"STK" }`,
  or `GET /v1/api/trsrv/stocks?symbols=AAPL`. Market-data calls are keyed by **numeric conid**, not ticker.

**Market data is account-agnostic** — identical whether you authenticate paper or live (it's gated by
your IBKR *data subscriptions*, not the account). So the harvest needs **read scope only, no order
permissions**, and stored bars need no paper/live tag. Fits `docs/DATA_CONTRACT.md` as a real bar/quote
provider (better than the yfinance stopgap).

## Safety (TradePro side)
TradePro runs IBKR **read-only** on the live account: a hard kill-switch (`IBKR:AllowOrders`, default
**false**) makes `PlaceMarketOrderAsync` return REJECTED **with zero HTTP** — no order can reach
`/iserver/account/{id}/orders`. A harvest service should likewise never request order scopes.

## Secret (`tradepro/ibkr`, AWS Secrets Manager, region eu-west-2)
Keys: `client_key_id`(=`main`), `private_key`, `public_key`, `mode`(`paper|live`),
`client_id_paper`/`credential_paper`/`account_id_paper`, `client_id_live`/`credential_live`/`account_id_live`.
(`ip` optional — auto-detected; no `certificate` needed.) Never commit any of these — repo is public.
