# Moving the light scheduled jobs off the MacBook

**Owner, 29 Aug 2026:** *"macbook was choosen for runnign llm"*, *"all these
light scedhued jobs can mive to lambda"*, *"they shd start now going to aws
lambda so not dependent on macbook"*, and *"just remember i need UI function t
trigger them as I need it"*.

## Why

33 launchd jobs currently depend on a laptop being awake — there is a
`com.tradepro.stay-awake` agent whose only purpose is to fight that. The Mac
earns its place for ONE thing, Ollama, which needs the hardware. Everything
else is there by accident of history.

**Not the existing EC2 box**: it auto-stops overnight, and the Indian-market
job runs at 04:00 BST when that box is asleep.

## What can move, and what cannot

| job | needs | verdict |
|---|---|---|
| `index_strangle_paper` | yfinance, SMTP | **moves** — no local dependency |
| `index_strangle_alert` | yfinance, SMTP | **moves** |
| `post_earnings_puts` | BarStore (S3 read-through) | **moves**, given bucket access |
| swing / momentum screens | BarStore + heavy pandas | **MEASURED 6 Sep — do not lift-and-shift, see below** |
| bar-cache harvest | IBKR session, large writes | **stays** — long-running, stateful |
| ollama / LLM | local model | **never** |

## Built and committed

* `lambda_handler.py` — one handler, a `JOBS` registry, and the SAME code path
  for a scheduled invoke and a UI invoke. Deliberately one entry point: a
  second one "just for the button" is how the two silently diverge.
* `Dockerfile.lambda` — container image, not zip+layer. pandas/numpy/pyarrow/
  yfinance exceed Lambda's 250MB layer limit, and the repo already builds and
  pushes images to ECR for the API, so this reuses a pipeline that exists.

`HOME` is repointed to `/tmp` because Lambda's filesystem is read-only
elsewhere. That makes the local ledger EPHEMERAL — fine for the alert, which
only needs today's strikes, and the reason the paper record pushes to the API
rather than trusting local state.

## NOT yet done — needs approval, because it creates billable resources

1. **ECR repo** `tradepro-jobs` in `eu-west-2` (same registry as the API,
   108703420282).
2. **Lambda function** from that image. 1024MB / 120s is a starting guess —
   pandas import alone is slow, so this wants measuring rather than assuming.
3. **IAM role**: CloudWatch Logs, plus `s3:GetObject` on the bar-cache bucket
   for `post_earnings_puts`.
4. **Secrets**: SMTP credentials. They live in `~/.tradepro/email-creds.json`
   on the Mac today; in Lambda they belong in Secrets Manager, NOT in env vars
   on the function.
5. **EventBridge rules** — one per job/schedule:
   - `index_strangle_paper` weekdays 04:00 and 14:00 BST
   - `index_strangle_alert` every 15 min during both sessions
   - `post_earnings_puts` weekdays 21:45 BST
6. **UI trigger**: the desk calls the API, the API invokes the Lambda. That
   reuses the existing auth rather than exposing a Function URL to the
   internet.
7. **Only then** unload the local launchd jobs — run both in parallel for a few
   days first, and compare outputs before trusting the new path.

## Cost

Three jobs, a few hundred invocations a month, seconds each. Comfortably inside
the Lambda free tier; ECR storage for one image is pennies. The reason to do it
is reliability, not cost.

---

# PROVISIONED — 29 Aug 2026

Account 108703420282, eu-west-2. Verified by a real invoke, not by the console
saying it exists.

| resource | value |
|---|---|
| ECR | `108703420282.dkr.ecr.eu-west-2.amazonaws.com/tradepro-jobs` (keeps last 5 images) |
| image | arm64 (Graviton), 290MB compressed |
| function | `tradepro-jobs` — 1024MB, 300s, arm64 |
| role | `tradepro-jobs-lambda-role` — Logs, S3 read on the bar-cache bucket, and `secretsmanager:GetSecretValue` on `tradepro/email-*` ONLY |
| secret | `tradepro/email` — the local creds file uploaded verbatim, so there is one schema and nothing to drift |

## Schedules (UTC; BST is UTC+1)

    tradepro-strangle-paper-india   cron(0 3 ? * MON-FRI *)        04:00 BST, before the Indian open
    tradepro-strangle-paper-us      cron(0 13 ? * MON-FRI *)       14:00 BST, before the US open
    tradepro-strangle-alert-india   cron(0/15 4-9 ? * MON-FRI *)   Indian session
    tradepro-strangle-alert-us      cron(0/15 14-19 ? * MON-FRI *) US session
    tradepro-post-earnings-puts     cron(45 20 ? * MON-FRI *)      21:45 BST, after the US close

## Two things that bit, recorded so they do not bite again

**Lambda rejects OCI manifests.** `docker build` under buildx emits an OCI
manifest LIST by default and `CreateFunction` fails with "image manifest,
config or layer media type ... is not supported". The fix is
`--provenance=false --sbom=false`, which yields
`application/vnd.docker.distribution.manifest.v2+json`.

**`put-targets` shorthand cannot carry JSON.** `Input={"job":"..."}` fails
parameter validation; the target must be passed as a file.

## Deliberately NOT done

The local launchd jobs are STILL RUNNING. Both paths execute for a few days and
their outputs get compared before anything is unloaded. Cutting over blind is
how you discover in a week that the 04:00 Indian job never fired.

## Still owed

The UI trigger: desk -> API -> `lambda:InvokeFunction`. The handler already
accepts `{"job": "..."}` from any caller, so this is an API endpoint and a
button, not new Lambda work.

## Measured, 6 Sep 2026 — why the paper sleeves did NOT move

Owner: *"we are not leveraging olama so i would prefer all to lambda"*, then
*"ensure we do not create more regression"*, and on the result:
*"ok dont lft and shift for now then"*.

**The Mac's stated justification is gone.** Ollama is running, holding 8GB of
gemma3:12b resident, and has produced FIVE verdicts — newest 1 August, 35 days
stale. So the reasoning in "Why" above no longer holds, and the question was
worth re-opening.

**Both sleeves RUN on Lambda.** Registered as `paper_swing_dryrun` /
`paper_equity_dryrun` and invoked by hand. They load the same 244-symbol
universe, drop the same 39 barred ETFs, enable the S3 bar cache and honour
`--placement-mode manual`. `ib_insync` is not needed: it is imported lazily and
the package works without it. Every dependency is a network call.

**They are too slow for the interval, and that is the blocker:**

| job | Lambda duration | Mac schedule | ceiling |
|---|---|---|---|
| `paper_equity_dryrun` | **658 s** (11 min) | every 900 s | 900 s |
| `paper_swing_dryrun`  | **579 s** (10 min) | every 900 s | 900 s |

65–73% of every interval, against a HARD 900-second kill. One cold start, one
laggy IBKR session or one yfinance retry storm and the job is killed mid-run —
at exactly the moment it would be placing orders.

**That is a worse failure mode than the laptop.** A sleeping Mac does not
trade. A Lambda killed at 900 s can place one leg and die.

A false alarm worth recording: the Lambda run logged `NO bars from any source`
for many symbols. The Mac logged the SAME warning 13,048 times on the same day.
It was Sunday; there are no bars. Not a regression — check the other side
before concluding.

### What would actually unblock it, in order

1. **Split fetch from decide.** Most of those 11 minutes is fetching bars for
   ~200 symbols. Batch the fetch (cacheable, S3) and leave decide-and-place —
   the half that matters — running in seconds.
2. **Fargate, or the EC2 box.** No 15-minute ceiling. EC2 auto-stops overnight,
   which is fine for US hours and not for India.
3. **Question the interval.** Ichimoku is a DAILY strategy on settled bars.
   Running it 26 times a session may be inherited habit rather than
   requirement. If once or twice a day suffices, an 11-minute job is entirely
   fine on Lambda and options 1 and 2 are unnecessary.

Option 3 is a question about the strategy, not the infrastructure, and would
make this trivial. It should be answered first.

### State left behind

- Both jobs stay registered, `_dryrun`, `manual` placement, **no `--push`, no
  EventBridge rule**. A test asserts neither can be armed while the Mac agents
  still run — a double-placed signal is the one regression that costs money.
- The Mac agents are UNTOUCHED and still doing the real work.
- The Lambda timeout was raised 300 s → 900 s. That only affects jobs that were
  previously being killed; nothing else changes behaviour.

## 21 Nov 2026 — live-risk moves (owner: "it could be a simple lambda")

Job `live_risk` = `tradepro-live-risk --push --email --via-api`. Read-only
(Flex, through the API's client — the one central IBKR connection), no Flex
credential on the function, no new IAM grant. Spots come from the bar store
(S3 read), events from the API, mail from `tradepro/email`.

To finish (needs an SSO login; not repo material):

    aws lambda update-function-configuration --function-name tradepro-jobs \
      --environment "Variables={...existing...,TRADEPRO_LIVE_NAV_USD=<account value>}"
    aws events put-rule --name tradepro-live-risk \
      --schedule-expression 'cron(50 22 ? * MON-FRI *)'      # 22:50 UTC = the Mac's 22:50 GMT; move to 21:50 in BST
    aws events put-targets --cli-input-json file://live-risk-target.json   # Input {"job":"live_risk"} — VERIFY Input after

Run beside the Mac lane for a few nights (both push the same artifact; the
later one wins and they agree), compare the run log, then unload
`com.tradepro.live-risk`. The desk check still runs on the Mac and grades the
card from the artifact's age, so it needs no change.

## 21 Nov 2026 — the 5-minute harvest moves (owner: "it should be on lambda")

Job `harvest_5m` = `tradepro-harvest-5m`: the Mac lane's `--resolution 5m`
call in Python (scope = harvest-flagged ∪ candidates ∪ paper book, 7-day
window), then intraday-shadow, intraday-setups and market-movers `--push`.
Bars come from `ibkr_web` through the backend; the store writes to S3.
Measured on the Mac: 214 symbols, 4.5 minutes.

    aws events put-rule --name tradepro-harvest-5m \
      --schedule-expression 'cron(0/10 14-21 ? * MON-FRI *)'   # US session, winter clock; 13-20 in BST
    put-targets Input {"job":"harvest_5m"} — VERIFY Input

Run beside the Mac lane for a session (same bars, same store), compare the
run log, then unload `com.tradepro.bar-cache-harvest-5m`.

### Same evening — movers split out

First Lambda run of `harvest_5m`: harvest 4m20s (201 GOLD), shadow+setups 8s,
market-movers 5m37s (cold S3: 400 days of closes × 214 names) = 605s. A reader
must not share the harvest's budget, so `market_movers` is its own job:

    tradepro-harvest-5m     cron(0/10 14-21 ? * MON-FRI *)   harvest + shadow + setups
    tradepro-market-movers  cron(5/15 14-21 ? * MON-FRI *)   movers, read-only

Bars are written per symbol as the harvest goes and every run re-covers the
trailing 7 days, so a symbol missed by a kill is caught by the next cycle and
the cockpit's per-symbol health names it meanwhile.

### Window (owner: "only the latest since it ran — that will be quicker")

Session runs fetch 2 days (today + the prior session); `harvest_5m_repair`
fetches 7 days once after the close:

    tradepro-harvest-5m-repair  cron(30 21 ? * MON-FRI *)

## 22 Nov 2026 — the short Mac lanes (owner: "leverage their infra")

Three lanes that are HTTP in, HTTP out, seconds each:

    job                 Mac lane                          schedule (UTC)
    ibkr_health_probe   com.tradepro.ibkr-health-probe    rate(15 minutes)
    refresh_universes   com.tradepro.refresh-universes    cron(30 7 * * ? *)
    trade_alerts        com.tradepro.trade-alerts         cron(7/15 * * * ? *)

`trade_alerts` CLOSES paper positions on stop / target / max-hold, so before
it could run anywhere but the laptop its dedupe ledger moved out of
`~/.tradepro/signal_watch_fired.json` into settings-kv (`trade_alerts_fired`):
the Mac lane and the Lambda job now share one memory and may overlap; the
first run that finds no key imports the laptop's file once. The order book is
also checked for an exit already enqueued against the same order, so a lost
ledger cannot produce a second close. The Lambda schedule sits 7 minutes off
the Mac's quarter-hours so the two never read the ledger in the same minute.
Mail goes through `resolve_smtp_creds` (file → Secrets Manager `tradepro/email`).

Run-log rows from the function carry `machine=lambda:tradepro-jobs`
(`TRADEPRO_MACHINE`, set in the image), so a Mac-vs-Lambda comparison is a
filter on one column.

    aws events put-rule --name tradepro-ibkr-health-probe --schedule-expression 'rate(15 minutes)'
    aws events put-rule --name tradepro-refresh-universes --schedule-expression 'cron(30 7 * * ? *)'
    aws events put-rule --name tradepro-trade-alerts      --schedule-expression 'cron(7/15 * * * ? *)'
    put-targets Input {"job":"<name>"} for each — VERIFY Input; add-permission for each rule

Run beside the Mac lanes for a day, compare `machine=` rows in the run log,
then unload the three plists (and drop them from desk-check's SCHEDULED_JOBS).

### Stays on the Mac, and why

| lane | reason | goes where |
|---|---|---|
| desk-check | grades launchd lanes, the Mac checkout and host power | rewritten last, when the Mac is empty |
| daily-digest | tails the paper lanes' logs on the Mac | moves with the placers |
| screener-daily | 21 min (options screen) | Fargate |
| bar-cache-audit | walks a local parquet tree | S3-aware rewrite, then Lambda |
| bar-cache-resource-intraday, harvest-daily, option-chain-capture, mirror | long | Fargate |
| paper-* placers | place orders; a kill mid-run can leave one leg | Fargate, after a resting broker stop |
| mac-deploy-sync, log-rotate, stay-awake | exist only because the others do | deleted with the last lane |

## 22 Nov 2026 — the long jobs: ECS Fargate, same image

Lambda's 900 s ceiling is the only reason the daily harvest (~1 h), the option
chain capture (~35 min) and the intraday re-source stayed on the Mac. They run
as **ECS Fargate scheduled tasks on the SAME `tradepro-jobs` image** this
workflow already builds (`:latest` is pushed alongside the SHA tag): the
container's entrypoint is overridden to `python -m <cli module>`, so the code
that runs on Fargate is the code that runs on Lambda, built once.

The infrastructure is Terraform in `ccit-infra` (`modules/tradepro-jobs-fargate`):
cluster, task definition (1 vCPU / 4 GB, public subnet, egress-only SG), task
role = the Lambda role's grants (bar-cache bucket RW, `tradepro/all` and
`tradepro/email` secrets), EventBridge Scheduler schedules in Europe/London so
the Mac's clock times carry over without a BST shift.

First job: `tradepro-harvest-daily` (this repo, `cli/harvest_daily.py`) — the
Mac's `bar-cache-harvest-daily.sh` chain step for step. **One writer:** the
Fargate schedule is created DISABLED, verified by a hand `run-task` with
`--no-email`, and enabled the same evening the Mac plist is unloaded — never
both on one night. `TRADEPRO_MACHINE=fargate:tradepro-jobs` on the task.
