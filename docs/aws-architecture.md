# AWS architecture

How TradePro is hosted on AWS, what runs where, and why the boundaries
are drawn the way they are. Pairs with `docs/aws-deploy.md` (the
operator guide).

## TL;DR

> Only the .NET API runs in AWS. The Python worker (which calls the
> LLM for rationales) stays on the user's Mac. This costs ~£3-4/mo
> instead of ~£30-100/mo, and keeps T212 + Finnhub keys off shared
> infrastructure.

## What runs where

```
 ┌─────────────────────────────────────────────────┐         ┌────────────────────────────────────────────────────────────┐
 │   USER's MAC (always on, free)                  │         │  GITHUB ACTIONS (OIDC → ccit-dev-energycosmos-deploy)      │
 │                                                 │         │                                                            │
 │   tradepro-worker (Python venv)                 │         │   aws-build-push   →  buildx ARM64 → ECR (api + frontend) │
 │    ↳ daily compare run (Yahoo OHLCV cache)      │         │   aws-redeploy     →  ship compose via SSM, pull, up -d   │
 │    ↳ Finnhub earnings key                       │         │   aws-set-env      →  upsert /opt/tradepro/.env via SSM   │
 │    ↳ LLM rationales (Ollama llama3.1:8b)        │         │   aws-start/stop   →  EC2 lifecycle + /health probe       │
 │    ↳ ~/.tradepro/credentials                    │         └───────────────────────────────────────────┬────────────────┘
 │      → api_base_url = http://<EIP>:8081         │                                                    │ SSM RunCommand
 │      → api_token    = INGEST_TOKEN              │                                                    │ no SSH ever
 │                                                 │                                                    ▼
 │   HTTPS POSTs to AWS:                           │            ┌───────────────────────────────────────────────────────┐
 │      /api/ingest/compare    (Bearer)            │ ────────►  │  AWS  eu-west-2 · account 108703420282                │
 │      /api/ingest/heartbeat  (Bearer)            │            │                                                       │
 │                                                 │            │   EC2 t4g.small  ccit-dev-tradepro-host (Graviton)    │
 └─────────────────────────────────────────────────┘            │   IMDSv2 · SSM-only access · no inbound SSH           │
                                                                │   Elastic IP: stable across stop/start                │
                            ┌──────────────────────────────┐    │                                                       │
                            │  BROWSER                     │    │   docker-compose.aws.yaml                             │
                            │   http://<EIP>/              │◄───┤    ↳ tradepro-frontend (nginx)  :80   ← Basic Auth   │
                            │    Basic Auth: admin/****    │    │       · serves built SPA                              │
                            │    nginx reverse-proxies     │    │       · /api/* → tradepro-api (no auth on /api)       │
                            │    /api/* → api container    │    │       · /opt/tradepro/.htpasswd mounted (file)        │
                            │                              │    │    ↳ tradepro-api (.NET 8)      :8081 host-exposed   │
                            │  Direct /api/* on :8081      │    │       · INGEST_TOKEN gate on /api/ingest/*           │
                            │  (worker, postman, curl)     │    │       · Trading212Client (key OR key+secret pair)    │
                            │   Bearer: INGEST_TOKEN       │◄───┤       · /data named volume (compare cache, sqlite)   │
                            └──────────────────────────────┘    │                                                       │
                                                                │   ECR: ccit-dev-tradepro-api · -frontend (ARM64)      │
                                                                │   SSM Parameter Store: /opt/tradepro/.env values      │
                                                                │   EventBridge: nightly stop @ 22:00 UTC               │
                                                                │   S3 (optional): ccit-dev-tradepro-archive            │
                                                                └───────────────────────────────────────────────────────┘
```

### Auth layers at a glance

| Surface | Who | Auth |
|---|---|---|
| `http://<EIP>/` (SPA shell) | end users (browser) | nginx Basic Auth (htpasswd) |
| `http://<EIP>/api/*` (SPA → API, same origin) | the SPA in the browser | none — basic auth turned off on this path so `fetch()` works without cached creds |
| `http://<EIP>:8081/api/ingest/*` (worker, curl) | Mac worker | Bearer `INGEST_TOKEN` |
| Trading 212 API (outbound from EC2) | the API container | HTTP Basic (key+secret) for older accounts, or raw key header for newer ones |

## Why this split

### LLM cost is the gravity

A 7-13B parameter LLM for rationales needs either:

| Option | Where | Cost |
|---|---|---|
| Hosted (Claude / GPT / Gemini) | API call | ~$5-20/mo for daily compares |
| Self-hosted on AWS GPU | g4dn.xlarge (Tesla T4) | ~£280-380/mo always-on; ~£30-100/mo with aggressive auto-stop |
| **Mac M-series with MLX** | local | **£0** (electricity already paid for) |

The Mac is already on, has unified memory that fits 13B models
comfortably, and gives 5-15 tokens/sec. There's no AWS architecture
that beats "free". This is the gating constraint — the worker has
to stay where the LLM is.

### Privacy bonus

`tradepro-worker` reads:
- Trading 212 API key (live portfolio access)
- Finnhub API key
- Cached Yahoo OHLCV for ~200 symbols (parquet on disk)
- The user's positions JSON

None of that needs to land on a shared cloud host. Local stays local.

### What AWS is for

The .NET API is the **read endpoint** the browser hits. It needs to
be:
- always-on during the user's day (auto-stop at 22:00 UTC keeps cost
  contained)
- reachable by a stable URL (Elastic IP)
- isolated from the worker's secrets (the API only sees the
  `INGEST_TOKEN`, never the T212 / Finnhub keys)

That's a tiny workload. A `t4g.small` (2 vCPU ARM Graviton, 2 GB RAM)
runs it with room to spare.

## Components, top to bottom

### Terraform (`~/sourcecode/ccit-infra`)

```
modules/tradepro-demo/
├── main.tf          EC2 + EIP + SG + IAM + EventBridge schedules
├── ecr.tf           ECR repo for the API image (+ lifecycle policy)
├── user-data.sh     First-boot bootstrap: docker, git clone, compose up
├── variables.tf
└── outputs.tf       instance_id, public_ip, api_url, ssm_command, ecr_api_url

accounts/infoccit-workloads/main.tf
└── module "tradepro_demo" { ... }    Wires the module into the account
```

Everything is declarative. `terraform apply` is idempotent — the only
manual step is creating the GitHub PAT in SSM Parameter Store
(deliberately outside terraform so the secret never lands in
tfstate).

### ECR (`ccit-dev-tradepro-api`)

Single repo, ARM64 only, image scanning on. Lifecycle: keep last 10
tagged images, delete untagged after 1 day. CI pushes both `:latest`
(rolling) and `:<sha8>` (pinned). The redeploy workflow defaults to
`:latest` but accepts a SHA for rollback / pinning.

### EC2 instance (`ccit-dev-tradepro-host`)

- AMI: Amazon Linux 2023 ARM (looked up dynamically; pinned via TF
  `lifecycle.ignore_changes` so AMI updates don't recreate the box).
- Storage: 16 GB gp3, encrypted.
- IMDSv2 mandatory.
- Instance role: `AmazonSSMManagedInstanceCore` + ECR pull (scoped to
  the one repo) + read on the SSM PAT path.
- No SSH ingress. Shell access via SSM Session Manager.
- systemd unit `tradepro.service` brings docker-compose back up on
  every boot (so a stop/start cycle doesn't need manual intervention).

### Networking

Reuses the foundation `networking` module: one VPC, one public
subnet, IGW. The TF module's security group opens **port 8081 to
0.0.0.0/0** (intentional — this is a publicly-accessible demo). Lock
down to your IP via `var.allowed_api_cidr` for private use.

### Secrets

Three places, three tiers:

| Secret | Where | Why |
|---|---|---|
| GitHub PAT (clone private repo) | SSM Parameter Store at `/ccit-dev/tradepro/github-deploy-pat`, SecureString | Out of tfstate; only the EC2 role can read it; rotate via `aws ssm put-parameter --overwrite` |
| AWS deploy creds | OIDC role assumed by GitHub Actions | No long-lived keys in repo; trust scoped to specific repo + branch + environment |
| API runtime config (T212 key, Finnhub key, INGEST_TOKEN, …) | `/opt/tradepro/.env` on the box, edited via SSM | Never committed; never in tfstate; lives only on the host disk |

### CI/CD (`.github/workflows/aws-*.yml`)

| Workflow | Trigger | What it does |
|---|---|---|
| `aws-build-push` | push to `main` (when `backend/` changes) or manual | Buildx → linux/arm64 → ECR; tags `:latest` + `:<sha8>` |
| `aws-redeploy` | manual | SSM RunCommand: git fetch, ECR login, update IMAGE_TAG in .env, `compose pull` + `up -d` |
| `aws-start` | manual | EC2 start; polls `/health` with timeout |
| `aws-stop` | manual | EC2 stop (also runs automatically nightly via EventBridge) |
| `aws-status` | manual | Instance state + `/health` probe + recent SSM history |

All five use the same OIDC role (`ccit-dev-energycosmos-deploy` —
name kept for backwards compat from when it was energycosmos-only).

## Cost model

| State | Monthly |
|---|---|
| **Stopped** (EBS only) | ~£1.30 |
| **Running auto-schedule** (08:00-22:00 UTC weekdays) | ~£3-4 |
| **Running 24/7** | ~£10-12 |
| **+ ECR storage** (10 image versions, ~150 MB each) | <£0.10 |
| **+ Data transfer out** (small) | pennies |
| **+ EIP attached to running instance** | free |
| **+ EIP attached to stopped instance** | free (only idle EIPs are charged) |

Add ~£5 from the foundation (GuardDuty + CloudTrail) which is shared
across all workloads in this account.

## Failure modes + recovery

| Failure | Blast radius | Recovery |
|---|---|---|
| Instance terminates | API down; data on the named volume **lost** | `terraform apply` rebuilds the box; user-data re-bootstraps; redeploy workflow re-pushes the .env (no — .env has to be re-pasted via SSM); compare cache rebuilds on first push from worker |
| AMI changes upstream | None — `lifecycle.ignore_changes = [ami]` pins the running AMI | Manually bump the AMI by removing it from `ignore_changes`, applying, putting it back |
| ECR image expired by lifecycle | Redeploy of an old SHA fails | Use `:latest`, or re-trigger `aws-build-push` to publish a fresh tag |
| GitHub PAT in SSM expires (90d default) | Fresh launches fail at user-data; existing instance fine | `aws ssm put-parameter --overwrite` with a new PAT |
| EBS volume corrupt | API down; no data loss if volume detaches cleanly | Snapshot → fresh volume → reattach; or `terraform apply` to recreate (loses compare cache) |
| Auto-stop fires during a long run | API goes down at 22:00 UTC | Set `auto_schedule = false` in the TF module for the run; or manually `aws-start` to resume |

## How this differs from energycosmos

| | energycosmos | tradepro |
|---|---|---|
| Service count | 6 modules + frontend (multi-tenant on one box) | 1 service (just the API) |
| ECRs | 4 (modules, frontend, strategy_py, orchestrator_net) | 1 (api) |
| Port | 8080 | 8081 (so both demos can run concurrently) |
| Repo | `sunnylnct007/energycosmos` | `sunnylnct007/tradepro` |
| OIDC role | `ccit-dev-energycosmos-deploy` (shared) | same role, repo added to the trust list |
| Auto-schedule | yes | yes (same defaults) |

Everything else is identical: same module pattern, same SSM-driven
redeploy, same auto-stop/start, same instance type, same AMI family.

## Roadmap (deferred)

See `ROADMAP.md` for full triggers + rationale. Highlights:

1. **Worker as a sidecar.** Today the worker stays on the Mac for LLM
   cost reasons (above). When LLM rationales move to a hosted model
   (Claude / GPT) the worker can run in the same compose stack as a
   `worker:` service driven by cron.
2. **Frontend on AWS.** Two paths: in-compose nginx (simpler, no CDN)
   or S3 + CloudFront (cheaper at scale). Frontend's
   `VITE_API_BASE_URL` already supports an env-driven URL — no code
   change needed, just the deploy.
3. **TLS.** ACM cert + Caddy / nginx in compose, or swap EC2 nginx
   for an ALB. Trigger: when a domain is decided.
4. **RDS.** When in-memory compose state across restarts becomes a
   real problem. db.t4g.micro Postgres ~£10/mo.
5. **OIDC role tighten.** Today the role has `*FullAccess` policies
   for EC2, IAM, VPC, SSM, ECR, S3, DynamoDB. Replace with a
   least-privilege custom policy once the deploy footprint is stable.

## Decision log

- **Why a separate `tradepro-demo` module instead of co-tenanting on
  the energycosmos box?** Cleaner lifecycle (stop one without
  stopping the other), no RAM contention on the 2 GB box, simpler
  troubleshooting. Cost difference: ~£3-4/mo vs £0. Worth it.
- **Why a `t4g.small` not a `t4g.micro`?** .NET runtime + image
  pull peaks at ~700 MB; `t4g.micro` (1 GB) leaves no headroom for
  spikes (compare write-back, healthcheck thrash, container restarts).
- **Why not Fargate / ECS?** Two services would justify ECS. One
  doesn't. Fargate's per-task minimum (~£15/mo for always-on) is
  more than the EC2 + auto-stop math.
- **Why HTTP not HTTPS?** No domain yet. The IP-based pattern matches
  energycosmos. TLS lands when a domain does.
- **Why SSM not SSH?** Auditable (CloudTrail logs every session); no
  key management; works through the security group without an open
  port.
