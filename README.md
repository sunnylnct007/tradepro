# TradePro

A personal trading-strategy platform. The core question it answers is:

> **Given a defined list of stocks and a strategy, what's worth buying or
> selling today — and how much money would that strategy have made?**

- **Scanner** — run a strategy over a watchlist, get ranked BUY / SELL / HOLD.
- **Signal detail** — drill into a single symbol: reasons, indicators (SMA, RSI,
  52-week range), suggested stop/target.
- **Simulations** — backtest the same strategy on historical data, see the
  equity curve, P&L, CAGR, drawdown and Sharpe. UK fee model (0.5% stamp duty)
  is on by default.

## Stack

- **Frontend** — React + Vite + TypeScript, deployed to Firebase Hosting
  (`smsp-291e3` / `showmesoldprice`).
- **Backend** — .NET 8 minimal API, deployable to Azure App Service. Pluggable
  data providers: **Yahoo Finance, Stooq, Binance** (all free, no keys).
- **Research** — Python package (`strategies/`) with the same backtest
  semantics as the API, tuned for heavy local runs on an Apple-silicon Mac.
- **Domain** — `showmesoldprice.*` fronts the web app.
- **Region** — UK-first. GBP default, LSE `.L` symbols, stamp duty built in.

## Repo layout

```
backend/         .NET 8 Web API
  TradePro.Api/
frontend/        React + Vite + TS (Firebase Hosting)
strategies/      Python research / backtest package
.github/workflows/
  firebase-hosting-deploy.yml   prod deploy on push to main
  firebase-hosting-preview.yml  preview channel on PRs
  azure-api-deploy.yml          .NET API deploy on push to main
ROADMAP.md       phased build plan
```

## Quick start (local)

Works out of the box — no Firebase project or Azure subscription needed. Auth
is automatically bypassed in development (`ASPNETCORE_ENVIRONMENT=Development`).

### Everything in one shot — Docker Compose

Start the whole local stack (API + frontend + persistent worker) in one command:

```bash
cp .env.compose.example .env        # one-off, before the first run
docker compose up --build -d        # build images + start in background
open http://localhost:5173          # frontend
open http://localhost:5080/health   # API health
```

The worker container runs `tradepro-compare --push` every 30 minutes for every
ETF universe and heartbeats the API every 5 minutes — so the "Mac alive" badge
on the Health page stays green continuously and the Compare cache is rarely
more than half an hour stale. No launchd needed for the daily-driver case.

Other commands you'll want:

```bash
docker compose logs -f api          # tail API logs
docker compose logs -f frontend     # tail frontend logs
docker compose logs -f worker       # tail the compare loop
docker compose restart api          # nudge the API after a failed reload
docker compose up -d --build worker # rebuild + restart worker after code changes
docker compose down                 # stop everything (keeps node_modules volume)
docker compose down -v              # stop + drop volumes (compare cache, worker state)
docker compose up frontend          # frontend only (run API natively for breakpoints)
docker compose up -d api frontend   # api + frontend only (skip the worker)
```

Both api + frontend hot-reload from your working tree (`dotnet watch run` for
the API, Vite HMR for the frontend). The worker rebuilds on demand —
`docker compose up -d --build worker` after a Python code change picks up
the new image.

The host's Ollama (running natively at `localhost:11434` for the M-series perf
advantage) is reached from inside the worker via `host.docker.internal:11434`.
Set `TRADEPRO_OLLAMA_HOST` in `.env` if you're on Linux without that alias.

#### Trading 212 + Finnhub integrations

Both off by default. Add to `.env` to enable:

```bash
# Trading 212 portfolio + instruments
# T212 public API uses a SINGLE API key (no secret). Generate in
# T212 app → Settings → API (Beta) → "Generate API key", tick the
# "View portfolio" scope (read-only is enough), copy the key.
TRADEPRO_T212_MODE=demo            # or `live` for real money
TRADEPRO_T212_API_KEY=<key>
# TRADEPRO_T212_API_SECRET — unused, retained for backwards compat
# with older .env files. T212 has no secret. Leave empty.

# Finnhub forward-earnings calendar
TRADEPRO_FINNHUB_API_KEY=<key>     # finnhub.io free tier (60 req/min)
```

Then `docker compose up -d --force-recreate api worker`. The Portfolio tab
in the UI lights up automatically; the daily digest gains a "What You Hold"
section cross-referenced against today's verdicts.

#### Email digest

The `tradepro-email` CLI sends a structured daily digest. Local Outlook on
macOS works out of the box (AppleScript transport, plain text only). For
HTML rendering (colour, tables, charts), use Gmail SMTP:

```bash
# Gmail App Password setup: myaccount.google.com → Security → 2-Step
# Verification on → App passwords → generate one for "TradePro".
python3 -c '
import json, os
p = os.path.expanduser("~/.tradepro/email-creds.json")
open(p, "w").write(json.dumps({
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_user": "your.gmail@gmail.com",
    "smtp_password": "PASTE_THE_16_CHAR_APP_PASSWORD",
    "from": "your.gmail@gmail.com",
    "to": ["recipient@example.com"]
}, indent=2))
os.chmod(p, 0o600)
'

uv run --project strategies tradepro-email   # sends via SMTP (default)
```

#### Production deployment (Azure) — parked

The platform is portable enough to deploy to Azure App Service (backend) +
Firebase Hosting (frontend) — workflows already wired in `.github/workflows/`.
Three Azure App Service config knobs are needed for full feature parity with
the local stack:

| Setting | Why |
|---|---|
| `Ingest__Token` | Mac → API push auth for the deployed compare cache |
| `Trading212__Mode` / `Trading212__ApiKey` / `Trading212__ApiSecret` | Enables Portfolio tab + T212 endpoints on the deployed API |
| `Finnhub__ApiKey` | Enables forward-earnings warnings on the deployed digest |

Deferred until the local flow is fully sound — see the [Roadmap](./ROADMAP.md)
"Phase A — Production Azure deployment" entry for the step-by-step.

### Backend (.NET 8)
```bash
cd backend/TradePro.Api
dotnet run
# Try:
# -> http://localhost:5080/health
# -> http://localhost:5080/api/marketdata/providers
# -> http://localhost:5080/api/watchlists/uk
# -> POST /api/signals/scan     { "watchlist": "uk", "strategy": "sma_crossover" }
# -> POST /api/signals/evaluate { "symbol": "BARC.L", "strategy": "sma_crossover", "lookbackDays": 365 }
# -> POST /api/simulations/run  (see the Simulations page in the UI)
```

### Frontend (React + Vite)
```bash
cd frontend
cp .env.example .env.local      # edit — only VITE_API_BASE_URL is required locally
npm install
npm run dev                     # -> http://localhost:5173
```

If `.env.local` has no `VITE_FIREBASE_*` values, the UI runs in "local mode"
(no sign-in button, no token on API calls) and the backend accepts it.

### Ask Claude about your portfolio (MCP server)

`tradepro-mcp` exposes the platform over Anthropic's Model Context Protocol
so any MCP-aware client — **Claude Desktop**, Cursor, or our own future
/chat page — can query your data with strict citation tracking and
fail-closed verification.

```bash
# In Claude Desktop's config (~/Library/Application Support/Claude/claude_desktop_config.json):
{
  "mcpServers": {
    "tradepro": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/tradepro/strategies", "tradepro-mcp"],
      "env": { "TRADEPRO_API_URL": "http://localhost:5080" }
    }
  }
}
```

Then in Claude Desktop:

```
@tradepro analyse_etf("QQQ")
@tradepro should_i_buy_today("etf_us_core")
@tradepro compare_etfs("VOO,VWRP.L")
```

The decomposition prompt forces tool-use before answering; the
`verify_answer` tool blocks any claim that doesn't trace to a tool
output. Every Q&A leaves a full trace at
`~/.tradepro/traces/<trace_id>.json` for audit. See
[Help → Ask Claude about your portfolio](https://github.com/sunnylnct007/tradepro/blob/main/frontend/src/docs/help-content.ts)
for the accuracy contract.

### Tests (Behave / BDD)

Local-only smoke tests for the rule layer (bucket logic, sentiment
demotion, schema validation, rationale fallback). The end-to-end
Yahoo + Ollama pipeline is intentionally **not** in the BDD suite —
it's a manual smoke test (`uv run tradepro-compare`).

```bash
cd strategies
uv run behave                              # 2 features, 6 scenarios, ~3ms
uv run behave features/schema.feature      # one file
```

Tests force `TRADEPRO_LLM=noop` so they're deterministic + network-free.
Pattern mirrors the SpecFlow / Gherkin style from the Volue / SWERVE
reference uploads — Given / When / Then, one file per concern.

### Data model

The comparator payload is now a versioned, validated schema —
`strategies/tradepro_strategies/schema/`. Every payload carries
`schema_version` (currently `1.0.0`); the producer-side validation
hook in `compare()` catches drift the moment a field changes shape
rather than waiting for a CI build to fail later.

TypeScript types are generated from the Python schema. After changing
a model, run:

```bash
uv run python tools/gen_ts_types.py    # writes frontend/src/api/types.generated.ts
```

Versioning policy: compatible additions don't bump (frontend keeps
working); breaking changes (field removed, type changed, semantics
shifted) bump the major.

### Strategies (Python + uv, M-series friendly)

Install [uv](https://docs.astral.sh/uv/) once (`brew install uv` or
`curl -LsSf https://astral.sh/uv/install.sh | sh`), then:

```bash
cd strategies
uv sync                                  # installs from uv.lock
uv run tradepro-backtest --symbol BARC.L --strategy sma_crossover \
    --from 2019-01-01 --capital 10000
```

Optional ML extras (`torch`, `scikit-learn`, `lightgbm`):

```bash
uv sync --extra ml
```

## Deploying

### Frontend → Firebase Hosting (`smsp-291e3`)

1. In Firebase Console → Project settings → Service accounts, generate a new
   private key (or run `firebase init hosting:github` locally and let it
   provision the secret for you).
2. In GitHub → repo → Settings → Secrets → Actions, add a secret named
   **`FIREBASE_SERVICE_ACCOUNT_SMSP_291E3`** with the JSON contents.
3. Push to `main` — the `firebase-hosting-deploy.yml` workflow builds and
   deploys the `frontend/` app to the `live` channel. PRs deploy to a
   preview channel that auto-expires in 7 days.

Firebase web config (`apiKey`, etc.) is in `frontend/.env.production` — it's
not secret (it ships in the client bundle), but lock it down in Google Cloud
Console with HTTP-referrer restrictions once the custom domain is live.

### Auth — Firebase ID tokens + UID whitelist

In production the API refuses anything without a valid Firebase ID token
issued by the `smsp-291e3` project, and it further restricts access to a
whitelist of Firebase user IDs (so only you can hit your API).

1. In Firebase Console → Authentication, enable the **Google** sign-in
   provider.
2. Sign into the deployed web app once with your Google account.
3. Grab your UID: Firebase Console → Authentication → Users → copy the UID
   for your account.
4. In the Azure App Service → Configuration, add an app setting:
   - `Firebase__AllowedUserIds__0` = `<your UID>`
   - (Add `…__1`, `…__2` for more users.)
5. The App Service should already have `Firebase__ProjectId=smsp-291e3` (it
   defaults to that via `appsettings.json`).

Leaving `Firebase__AllowedUserIds` empty means **any** signed-in Firebase user
on this project can call your API — so set it before going public.

### Backend → Azure App Service (`tradepro-api`)

1. Create a **Linux, .NET 8, Code-based** App Service named `tradepro-api`
   (UK South keeps latency low). Avoid container-based publishing —
   the workflow uses a zip deploy.
2. App Service → **Get publish profile** → paste the XML into the GitHub
   secret **`AZURE_WEBAPP_PUBLISH_PROFILE`**.
3. App Service → **Environment variables**, add:
   - `ASPNETCORE_ENVIRONMENT` = `Production`
   - `Firebase__AllowedUserIds__0` = `<your Firebase UID>` (from Firebase
     Console → Authentication → Users after you sign in once).
   CORS is handled by the code (`appsettings.json`) — do **not** add
   anything under App Service → API → CORS, or it will conflict with
   the in-code middleware.
4. Push to `main` → the `azure-api-deploy.yml` workflow builds, publishes,
   and deploys.

`VITE_API_BASE_URL` in `frontend/.env.production` points at
`https://tradepro-api.azurewebsites.net` — verify against the new App
Service's Overview URL (Azure sometimes appends a random subdomain) and
edit if needed. Later, map a custom domain like
`https://api.showsoldprice.com` and swap the env var.

## Design principles

- **Data is the foundation.** Providers are pluggable; no symbol, list or
  strategy is hardcoded into business logic. Adding a provider = one class
  + one DI line.
- **Defined universes, not the whole market.** You pick the watchlist — the
  scanner ranks *within* it. Start with `uk`, add your own.
- **Backtest and live signals share the same code paths.** Whatever the
  scanner recommends today is what the simulator would trade in backtests.
- **Reproducible research.** Python runs locally on the M4 and emits JSON the
  API can ingest; backtest results are deterministic given config + data.
- **Honest about uncertainty.** Confidence is displayed, not hidden. No
  claims of alpha that haven't been measured.

See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the full system
description (components, data flows, indicators like SMA/RSI/MACD in plain
English, security posture, observability model).

See [ROADMAP.md](./ROADMAP.md) for the phased plan.
