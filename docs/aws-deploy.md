# AWS deploy — operator guide

How to ship the TradePro .NET API to AWS and keep it running. Mirrors
the energycosmos pattern: single t4g.small EC2 in eu-west-2, accessed
by IP, image pulled from ECR, controlled via GitHub Actions + SSM.

**Cost target:** ~£3-4/month with the auto-schedule on (stops 22:00
UTC, starts 08:00 UTC weekdays); ~£10/month if you turn the schedule
off and run 24/7. EBS-only when stopped: ~£1.30/month.

## Topology

```
┌──────────────── GitHub Actions ────────────────┐
│  aws-build-push  →  ECR (ccit-dev-tradepro-api)│
│  aws-redeploy    →  SSM RunCommand on EC2      │
│  aws-{start,stop,status}                       │
└─────────────────────────────────────────────────┘
                       │ OIDC role
                       ▼
┌─────────────────── eu-west-2 ───────────────────┐
│  EC2 t4g.small  (ccit-dev-tradepro-host)        │
│  ↳ Elastic IP (preserved across stop/start)     │
│  ↳ docker compose -f docker-compose.aws.yaml    │
│     ↳ tradepro-api:5080  ← exposed on host:8081 │
└─────────────────────────────────────────────────┘
```

## One-time setup (in this order)

### 1. Create the GitHub deploy PAT in SSM

Fine-grained PAT, **read-only on `sunnylnct007/tradepro`** (Contents:
Read; Metadata: Read). The terraform module reads this to git-clone
the private repo from EC2 user-data.

```bash
aws ssm put-parameter \
  --name /ccit-dev/tradepro/github-deploy-pat \
  --type SecureString \
  --value <PAT> \
  --region eu-west-2
```

### 2. Apply the terraform

In `~/sourcecode/ccit-infra`:

```bash
export AWS_PROFILE=infoccit-admin
terraform -chdir=accounts/infoccit-workloads init
terraform -chdir=accounts/infoccit-workloads plan
terraform -chdir=accounts/infoccit-workloads apply
```

This provisions the EC2, EIP, ECR repo, IAM, SG, and EventBridge
schedules. Outputs include `tradepro_api_url` and `ecr_tradepro_api_url`.

The instance bootstraps via user-data:
- installs docker + compose
- clones the tradepro repo to `/opt/tradepro`
- writes a minimal `/opt/tradepro/.env` (just `ECR_REGISTRY` +
  `IMAGE_TAG=latest` + `API_HOST_PORT`)
- pulls the latest image from ECR (will fail until step 3)
- registers the systemd `tradepro.service`

### 3. Build + push the first image

In GitHub Actions on `sunnylnct007/tradepro`, run **aws-build-push**
manually. It builds an ARM64 image and pushes `:latest` + `:<sha>`
to ECR. After this, the systemd service can pull successfully.

### 4. Drop the production .env on the box

Shell in via SSM (no SSH key needed):

```bash
aws ssm start-session --target <instance_id> --region eu-west-2
sudo cp /opt/tradepro/.env.aws.example /opt/tradepro/.env
sudo nano /opt/tradepro/.env   # fill in INGEST_TOKEN, T212/Finnhub keys, CORS origin
sudo systemctl restart tradepro
```

The `.env` file is **only on the box** — never committed, never in
tfstate. Loss-of-host = re-paste from your password manager.

### 5. Verify

```bash
# from anywhere:
curl http://<public_ip>:8081/health
```

Should return 200. The `tradepro_api_url` terraform output gives you
the URL. From the GitHub Actions side, run **aws-status** for a
one-shot check.

## Routine ops

| You want to … | Run |
|---|---|
| Ship a code change | push to `main` → `aws-build-push` runs automatically; then `aws-redeploy` (defaults to `image_tag=latest`) |
| Pin a specific build | `aws-redeploy` with `image_tag=<sha8>` |
| Start the box | `aws-start` (auto-waits for `/health`) |
| Stop the box | `aws-stop` (or just wait for the 22:00 UTC auto-stop) |
| See current state | `aws-status` |
| Shell in | `aws ssm start-session --target <id> --region eu-west-2` |
| Tail container logs | (via SSM) `cd /opt/tradepro && docker compose -f docker-compose.aws.yaml logs -f --tail=200 api` |
| Change an env var | Edit `/opt/tradepro/.env` over SSM, then `sudo systemctl restart tradepro` |

## Adding the worker / frontend later

Both are easy increments on this skeleton:

**Worker.** Add a `worker:` service to `docker-compose.aws.yaml`,
build a `strategies/Dockerfile.production` (mirror
`Dockerfile.worker`), add a `STRATEGIES_REPO` to the
`aws-build-push.yml` matrix, add an `aws_ecr_repository.strategies`
to the terraform module, redeploy.

**Frontend.** Two paths:
- **In-compose nginx** (cheaper, no CDN): add a `frontend:` service
  serving the Vite build behind nginx; map host:80 → container:80;
  same TF SG ingress rule + same redeploy workflow.
- **S3 + CloudFront** (CDN, cheaper at scale, $1-2/month): add an
  `aws_s3_bucket` + `aws_cloudfront_distribution` to the terraform
  module; replace the build-push step with `aws s3 sync ./dist
  s3://...` + `aws cloudfront create-invalidation`. The frontend's
  `VITE_API_BASE_URL` points at the EC2 IP regardless.

## Troubleshooting

**`aws-redeploy` fails with "No running instance found".** Run
`aws-start` first. The auto-stop at 22:00 UTC catches you out if
you redeploy late at night.

**`docker pull` errors with `no basic auth credentials` on the box.**
The instance role lost ECR perms or the ECR token expired (12-hour
lifetime). The redeploy workflow re-runs `aws ecr get-login-password`
on every redeploy, so this self-heals — manual fix is the same one
line via SSM.

**`/health` returns 404 but the container is up.** The .NET API binds
to `5080` inside the container; check `Program.cs` hasn't moved
`/health` behind auth (`app.MapGet("/health", ...).AllowAnonymous()`).

**The PAT in SSM expired.** Fine-grained PATs default to a 90-day
lifetime. Symptom: user-data fails on the first git clone after a
fresh launch. Rotate the PAT in GitHub, then `aws ssm put-parameter
--overwrite ...` to update.

## What's deferred

See `ROADMAP.md` for the full list. Highlights:
- TLS via ACM + Caddy / nginx (when a domain lands)
- Worker as a sidecar container or scheduled Fargate task
- Frontend on S3 + CloudFront (when frontend deploy is needed)
- RDS for persistent state (when in-memory state across compose
  restarts becomes a real problem)
- Tighten the OIDC role's `*FullAccess` policies to least-privilege
