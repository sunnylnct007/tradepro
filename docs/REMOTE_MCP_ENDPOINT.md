# Remote MCP endpoint — read-only TradePro for any agent

**Status: LIVE** since 13 Sep 2026. Verified on the live URL — bare
`/mcp` 404, `/mcp/<token>` GET 406, `tools/list` 101 tools with zero
mutating, `tools/call get_health` returning a real payload from
`api:5080`.

## What this fixes

The registered connectors (`TradePro-Web`, `tradepro-Aws`) pointed at
`https://tradepro.showsoldprice.com` — the React SPA origin, behind
HTTP Basic auth. `/mcp` returned `401 WWW-Authenticate: Basic
realm="TradePro"`, so the MCP handshake never began and Claude rendered
it as *connected, no tools*.

There was nothing to connect to anyway. TradePro's MCP server has only
ever run over **stdio**, spawned by `uv` on the Mac. That is why it
works in a Claude Code session with the desktop link and nowhere else.
This adds the missing component: an HTTP transport, deployed.

Two diagnoses were offered before this and both were wrong. It was not
OAuth, a stale registration, or a per-chat toggle. It was not a dead
`cloudflared` either — `~/.cloudflared/config.yml` serves `openclaw`
and `ollama` and has never carried tradepro traffic; the hostname
resolves straight to the EC2 elastic IP. The `Connection refused` seen
on Saturday was the box being **stopped for the weekend**, which is
normal: `aws-scheduled-start.yml` runs `50 12 * * 1-5`.

## The surface is read-only, and that is enforced

`build_server(read_only=True)` removes 12 tools, 113 → 101:

| Why removed | Tools |
|---|---|
| Moves a position / releases an order | `approve_paper_order`, `reject_paper_order`, `set_paper_placement_mode`, `close_option_leg`, `flatten_short_options`, `run_paper_session` |
| Changes what the desk will trade | `apply_paper_override` (PAUSE/RESUME/VETO/FORCE_CLOSE), `configure_paper_llm_gate`, `update_paper_strategy_config` |
| Writes a shared store | `record_strangle_manual_trade`, `ibkr_fetch_bars` (needs the single IBKR market-data session the live desk holds), `run_comparison` (`push=True` overwrites the Compare cache) |

Two guards, because a denylist alone fails open:

1. A name in `MUTATING_TOOLS` that is **no longer registered** aborts
   startup. Renaming a mutating tool without updating the list is a
   crash, not a silent re-exposure.
2. Any surviving tool whose name starts with a mutating verb
   (`place_`, `close_`, `set_`, `flatten_`, `cancel_`, …) and is not
   explicitly exempted aborts startup.

`tests/test_mcp_read_only_surface.py` pins both, and
`aws-build-push` runs it **before** pushing the image — a build that
could expose a trading tool cannot be published.

The Mac keeps the full 113-tool surface over stdio. Nothing about
`tradepro-mcp` changed.

## Access control: a secret path segment

claude.ai's connector UI takes a URL and nothing else — no header
field, no API key field. So the shared secret rides in the URL:

```
https://tradepro.showsoldprice.com/mcp/<TRADEPRO_MCP_PATH_TOKEN>
```

The container answers on that path only. Bare `/mcp` and any wrong
token return **404** — verified, not assumed.

This is a bearer secret in a URL, with the weaknesses that implies: it
lands in browser history and in any proxy log along the way. It is an
accepted trade for a surface that cannot place an order. **Do not widen
the surface without replacing this with real auth.**

The token is never in this repo — the repo is public. It lives in
`/opt/tradepro/.env`, written from the `TRADEPRO_MCP_PATH_TOKEN` GH
secret by `aws-set-env`.

## The landmine, and it is not hypothetical

**The EC2 role must be allowed to PULL the mcp repo. Creating the repo
is not enough.** Inline policy `ccit-dev-tradepro-ec2-ecr-pull` on role
`ccit-dev-tradepro-ec2-20260510220637811100000002` scopes
`ecr:BatchGetImage` to a Resource **list** of individual repos. A repo
missing from it is denied, worded as *"repository does not exist or may
require docker login"* — which reads as a missing repo. It is not.

That denial **took the whole site down on 13 Sep**, because
`aws-redeploy` runs `up -d --remove-orphans --force-recreate`, and
force-recreate tears containers down *before* it pulls. One unpullable
image left nothing running.

Two guards now, so it cannot recur:

- `mcp` sits behind `profiles: ["mcp"]`, so a plain `up -d` skips it.
- `aws-redeploy` brings the site up first, then starts mcp as a separate
  **best-effort** step. An MCP failure prints a warning naming this
  policy and cannot fail the deploy.

⚠ **The current IAM fix was applied with the CLI, not terraform.** That
role is TF-managed, so the next `terraform apply` reverts the Resource
list — the pull starts failing and the endpoint dies at the deploy after
that, long enough later that the cause is easy to miss. **Put the mcp
repo ARN in the TF module.**

## Rotating the token

Regenerate (`openssl rand -hex 24`), update the GH secret
`TRADEPRO_MCP_PATH_TOKEN`, run `aws-set-env` — it rewrites `/opt/tradepro/.env`
and recreates the mcp container. Then update the connector URL. If the
secret is unset the endpoint is served with **no access control** and the
container warns at startup; that is correct only on a private network.

## Registering it

Claude.ai → Settings → Connectors → Add custom connector, URL as above.
**Update the existing `TradePro-Web` / `tradepro-Aws` entries** rather
than adding a third; both currently point at the SPA origin and will
keep failing.

Claude Code:
```
claude mcp add --transport http tradepro-remote \
  https://tradepro.showsoldprice.com/mcp/<token>
```

## Verifying it, from the live URL not from CI

```
curl -sS -X POST https://tradepro.showsoldprice.com/mcp/<token> \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Expect 101 tools. Two things worth asserting rather than eyeballing:
`flatten_short_options` must be **absent**, and bare `/mcp` must 404.

## The limit you will hit

**The endpoint is up only when the EC2 box is up — Mon–Fri from 12:50
UTC.** Nights and weekends it is off by design, and a browser session
will show the connector failing exactly as it did on Saturday. Start
the box with the `aws-start` workflow when you need it out of hours.

Nothing here depends on the MacBook any more, which was the point.
