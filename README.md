# Data Labeler

Image bounding-box labeling for globally distributed (English-speaking) teams. Users upload batches of images, drag boxes over objects, name each entity, and submit. A **different** user then reviews and accepts or rejects the work.

**Stack:** React 19 + TypeScript (Vite), Python FastAPI + SQLAlchemy 2 (async) + Postgres, AWS (Cognito, S3, CloudFront, RDS, EKS), Kubernetes (Kustomize), Terraform.

## Guarantees and how they are enforced

| Requirement | Mechanism |
| --- | --- |
| Only one user per image | Postgres lease. Claim is one atomic `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)`. Leases last 15 min, the UI heartbeats every 60 s, and a reaper CronJob returns expired leases to the queue. Writes need a live lease owned by the caller, otherwise `409 lease_lost`. A per-user advisory lock serializes double-clicks. No state lives in a pod. |
| Reviewer differs from annotator | The review claim excludes `submitted_by = me` and anyone in `image_submissions` (all prior attempts). Two plpgsql triggers (migration `0002`) reject a self-review at the database even if a code path is missed. |
| No duplicate images | `UNIQUE (batch_id, sha256)`. Duplicates are flagged and removed when an upload completes. |
| No duplicate boxes or labels | Same normalized label with IoU > 0.9 returns `422 duplicate_annotations`. A deferrable unique fingerprint is the DB backstop. Label names are normalized (trim, collapse spaces, lowercase) and unique per batch. |
| Safe retries on flaky links | Annotation saves are idempotent bulk upserts with client-generated UUIDs. |
| Global performance | Browsers upload straight to S3 over presigned URLs (optional Transfer Acceleration). The SPA and images are served by CloudFront from edge locations. |

### Image lifecycle

```
uploading -> pending -> in_progress -> submitted -> in_review -> accepted
                 ^           |  ^                      |
                 | release   |  +---- rework <---------+ (rejected, reason required)
                 +-----------+                         
                 escalated: after 3 rejections, goes to the admin queue (/requeue)
```

Rework is reserved for the original annotator for 24 h, then open to any labeler who has not reviewed that image. Skipping returns an image to the pool and the skipper never gets it again. Export (COCO JSON) includes only accepted images unless `include_unreviewed=true`.

Roles come from the Cognito groups `admin`, `labeler`, `reviewer`. `admin` implies all roles but is held to the same different-user rule.

## Repository layout

```
backend/            FastAPI app, Alembic migrations, pytest suite (real Postgres + moto S3)
backend/loadtest/   Concurrency load test (hundreds of labelers and reviewers)
backend/scripts/    Migration safety checker (used by CI)
frontend/           React SPA, Vitest tests, Playwright e2e (Chromium, Firefox, WebKit, phones)
deploy/k8s/         Kustomize: base (incl. NetworkPolicies), staging and prod overlays, migration Job
infra/terraform/    Core stack: VPC, EKS, RDS, S3, CloudFront, WAF, Cognito, ECR, IAM, alarms
infra/terraform/addons/  Cluster add-ons (load balancer controller, External Secrets); applied second
docs/runbooks/      Rollback and alarm runbooks
scripts/            dev-stack.sh, smoke-test.sh (post-deploy checks)
.github/workflows/  CI, deploy (staging then production), reusable deploy-environment, nightly load test
docker-compose.yml  Local Postgres, LocalStack S3, API, reaper
```

## Run locally

Requires Docker, Node 22+.

```bash
docker compose up -d --build      # Postgres, LocalStack S3, migrations, API on :8000, reaper
cd frontend && npm ci && npm run dev   # http://localhost:5173
```

Local auth runs in `AUTH_MODE=dev`: the login page offers a dev sign-in where you pick an email and roles (`POST /dev/login` exists only in this mode). Open the app in two browsers as different users to see the one-user-per-image behavior, and use a third as a second reviewer.

Without Docker, `scripts/dev-stack.sh` runs migrations and the API against a Postgres you provide and an S3-compatible endpoint such as `moto_server`.

## Tests

```bash
# Backend: needs a Postgres with a database named labeler_test (compose creates it).
# If something already uses host port 5432, start compose with DB_PORT=55432 and adjust the URL.
cd backend && pip install -e ".[dev]"
TEST_DATABASE_URL=postgresql+asyncpg://labeler:labeler@localhost:5432/labeler_test pytest
ruff check . && ruff format --check .

# Frontend
cd frontend
npm run lint        # includes func-style: const arrow functions only, no `function` declarations
npm run typecheck
npm test
npx playwright install chromium firefox webkit
npm run e2e         # needs the compose stack running; see below for what each project covers
```

Playwright projects: `chromium`, `firefox` and `webkit` run the desktop specs (workflow, concurrency, token expiry, admin). `mobile-chrome` (Pixel 7) drives the labeling canvas with real touch input injected through CDP. `mobile-safari` (iPhone 14, WebKit) runs the same spec with synthetic touch pointer events, because Playwright has no real touch injection for WebKit, so it proves our handlers and layout in WebKit but not Safari's own touch pipeline. `slow-network.spec.ts` (Chromium only) throttles the link and checks upload retry after dropped connections, that heartbeats survive an outage without losing the lease, and that a failed submit keeps the boxes.

```bash
# Workflow files and the backend migrations are checked in CI as well:
docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:1.7.12
docker run --rm -v "$PWD":/repo -w /repo ghcr.io/zizmorcore/zizmor:1.31.1 --no-progress .github
cd backend && python -m scripts.check_migrations static
```

The backend suite covers concurrent claims (25 users, 20 claimants for 5 images), reviewer independence including users who hold both roles, the rework and escalation loop, idempotent saves, dedupe, and auth. The Playwright suite covers two users draining a queue without overlap and the full upload, label, reject, rework, accept flow with independent reviewers.

## Deploy to AWS

1. **Infrastructure**
   ```bash
   cd infra/terraform
   cp terraform.tfvars.example terraform.tfvars   # set cognito_domain_prefix, optional domain and github_repository
   terraform init && terraform apply
   ```
   Configure a remote state backend in `versions.tf` first for real use. This core stack creates the cluster and IAM roles but installs nothing inside the cluster, so the first apply does not depend on the cluster being reachable.
2. **Cluster add-ons (once, after step 1)**. A separate stack looks up the cluster and IAM roles by name, then installs the AWS Load Balancer Controller, External Secrets Operator, and the `ClusterSecretStore`:
   ```bash
   cd infra/terraform/addons
   terraform init && terraform apply     # uses the same name/environment/region variables as the core stack
   aws eks update-kubeconfig --name $(cd .. && terraform output -raw cluster_name)
   ```
   Apply order is always core, then `addons`. Destroy in the reverse order.
3. **Fill in the overlays** (`deploy/k8s/overlays/prod` and `overlays/staging`) from `terraform output`: IRSA role ARN, Cognito pool and client IDs, bucket, CloudFront domain and key id, secret name, ACM certificate, API hostname, and the ECR image name (`${name}-${environment}-backend`). Create a DNS record for each API host pointing at its ALB. Staging is a second, independent Terraform stack and state (`infra/terraform/staging.tfvars.example`).
4. **GitHub Actions and promotion**. Every push to `main` that passes CI runs `deploy.yml`: it deploys **staging** (build and push the image, run the migration Job, roll out the API and reaper, sync the SPA, invalidate CloudFront), runs `scripts/smoke-test.sh`, and then waits for approval before doing the same for **production**. Rollouts that fail are undone automatically (`kubectl rollout undo`). Per-environment values (`AWS_DEPLOY_ROLE_ARN`, `EKS_CLUSTER_NAME`, `ECR_REPOSITORY`, `WEB_BUCKET`, ...) are variables on the GitHub *environments* `staging` and `production`, and the OIDC trust policy only accepts `repo:OWNER/NAME:environment:<name>`. The approval gate cannot be declared in a file: create the `production` environment under Settings, Environments and add **required reviewers**. See `docs/runbooks/rollback.md`.

Secrets (database URL, CloudFront signing key) are generated by Terraform into Secrets Manager and synced into the cluster by External Secrets. Pods use IRSA, so no AWS keys are stored.

Create the first admin in the Cognito pool and add them to the `admin` group; after that, admins invite users and manage roles from the app (Users page).

### What the deployment adds beyond the app

- **Startup guard.** `ENVIRONMENT` is `local`, `staging` or `production` and defaults to `production`. Outside `local` the API refuses to start with `AUTH_MODE=dev`, `DEV_AUTH_SECRET`, `JWKS_JSON`, a missing Cognito pool, or anything but `DB_SSL_MODE=verify-full` (the RDS CA bundle ships in the image). CI renders both overlays and fails if either contains dev auth.
- **Edge.** A regional WAF on the ALB and a CloudFront WAF (AWS managed rule groups plus a rate rule; start in count mode with `waf_block_mode=false`), and a CloudFront response-headers policy with a strict CSP, HSTS and `nosniff`. The CSP allows only the API origin and the image origin, so a new third-party origin has to be added in `cloudfront.tf`.
- **Network.** Kubernetes NetworkPolicies deny everything by default and allow only DNS, Postgres, HTTPS to the internet (not private ranges), and ingress from the ALB and the monitoring namespace. They are enforced by the VPC CNI network-policy agent, which `eks.tf` enables.
- **Observability.** Structured JSON logs with a request id, Prometheus `/metrics` (404 through the ALB, in-cluster only) including queue gauges, and a reaper heartbeat emitted as an EMF log line and, with `METRICS_SINK=cloudwatch`, `PutMetricData`. CloudWatch alarms notify an SNS topic (`alarm_email`): reaper silent or failing, ALB 5xx, RDS CPU, connections and storage, oldest submitted image too old. What each alarm means and what to do is in `docs/runbooks/alarms.md`.
- **Migrations are expand/contract.** CI runs `scripts.check_migrations`: a static pass rejects destructive operations (drop, rename, NOT NULL without a default, type change) unless the migration is marked `# expand-contract: approved`, and a round trip applies up, down, up on a scratch database and diffs the schema. So the previous release keeps working against the new schema during a rollout, and a rollback never needs a down migration.
- **Smoke test.** `scripts/smoke-test.sh API_URL [WEB_URL]` checks `/readyz`, `/healthz`, that anonymous calls get 401, that `/metrics` is not public, and (with a web URL) the CSP, HSTS and `nosniff` headers. Locally set `EXPECT_METRICS_BLOCKED=0` and omit the web URL (Vite does not send those headers).
- **Connections.** uvicorn runs with `--timeout-keep-alive 75`, above the ALB idle timeout (60 s), so the ALB never reuses a connection the pod already closed.

### Administration

Admins can archive a batch (all leases return to their queues and nothing can be claimed or uploaded until it is unarchived; exports still work), unarchive it, and delete it permanently. Deletion needs the batch to be archived and, in production, a cooldown (`BATCH_DELETE_COOLDOWN_SECONDS`, default the presign expiry) so no presigned upload is still valid. It removes S3 objects (including versions) and rows in resumable chunks and the UI keeps calling until it is done. The Users page invites users, changes roles and disables or re-enables accounts through the Cognito admin API (IRSA policy in `ecr_secrets.tf`). Cognito stays the source of truth; locally a fake in the API process stands in for it. Disabling is immediate (the API returns `403 account_disabled` on the next request) and a role change makes older ID tokens answer `401 token_stale`, so the client refreshes and picks up the new groups. The last admin cannot be demoted or disabled and you cannot change yourself.

## Database connections

Postgres connections are the scarcest shared resource, so the pool is sized from a budget rather than guessed:

| Quantity | Value | Where |
| --- | --- | --- |
| RDS `max_connections` (`db.t4g.medium`, 4 GiB) | about 400-450 (check `SHOW max_connections`) | `LEAST(DBInstanceClassMemory/9531392, 5000)` |
| Held back for the reaper, migration Job, admin and monitoring | at least 60 | |
| `DB_CONNECTION_BUDGET` (what the API pods may use) | 340 | `config.py` |
| `MAX_API_PODS` (HPA maximum) | 20 | `config.py`, prod overlay |
| Per pod | 340 / 20 = **17** | `Settings._per_pod_budget` |
| Of which the export pool (`EXPORT_POOL_SIZE`) | 2 | |
| Main pool + overflow | 15 (pool 9, overflow 6) | `effective_pool_size`, `effective_max_overflow` |

All pods at the HPA ceiling therefore hold at most 340 connections, and the reaper and migration Job fit in the remainder. If you raise `MAX_API_PODS` (or move to a smaller instance), raise or lower `DB_CONNECTION_BUDGET` in step; `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` override the derived values when you need to.

When every connection is busy a request waits at most `DB_POOL_TIMEOUT` seconds and then gets `503 db_busy` with `Retry-After`, instead of queueing without bound. The `RDS connections` alarm (`docs/runbooks/alarms.md`) fires well before the instance limit.

COCO export streams from its own two-connection pool so a slow download cannot starve labelers and reviewers. It reads one consistent snapshot (`REPEATABLE READ`) page by page with keyset pagination (`EXPORT_PAGE_SIZE`), so memory stays flat for any batch size.

## Load test

`backend/loadtest/run.py` drives hundreds of simulated labelers and reviewers through the real flow (upload, claim, heartbeat, submit, review) and fails if an invariant breaks: an image handed to two people at once, a self-review, an image that never reaches a terminal state, any 5xx/429/transport error, a lost lease despite heartbeats, or more database connections than budgeted.

```bash
docker compose up -d --build
cd backend && pip install -e .
python -m loadtest.run --labelers 300 --reviewers 100 --images 1500 \
  --database-url postgresql://labeler:labeler@localhost:5432/labeler --max-db-connections 20
```

It prints client-observed latency percentiles and, from `/metrics`, the handler latency the server measured (the two differ by the load generator's own scheduling). Local result for 300 + 100 users against one API pod (laptop, Docker): 1500 images through review in about 58 s, 25k requests, zero 5xx, zero 503, peak 15 database connections (the pool limit), no duplicate assignments. Server-side handler means under that load: claims 160-215 ms, heartbeats 260 ms, submit 630 ms, which is what a saturated 15-connection pool looks like; more pods add capacity linearly until the connection budget above is the limit. The `Load test` workflow runs the same thing nightly and on demand.

## Configuration

Backend settings are environment variables (see `backend/app/config.py`). Core: `ENVIRONMENT`, `DATABASE_URL`, `DB_SSL_MODE`, `AUTH_MODE` (`cognito` or `dev`, dev only when `ENVIRONMENT=local`), `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `S3_BUCKET`, `S3_USE_ACCELERATE`, `CLOUDFRONT_DOMAIN`, `CLOUDFRONT_KEY_PAIR_ID`, `CLOUDFRONT_PRIVATE_KEY_PEM`, `CORS_ORIGINS`. Workflow: `LEASE_MINUTES`, `MAX_REJECTIONS`, `REWORK_RESERVE_HOURS`, `DUPLICATE_IOU_THRESHOLD`. Capacity: `DB_CONNECTION_BUDGET`, `MAX_API_PODS`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, `EXPORT_POOL_SIZE`, `EXPORT_PAGE_SIZE`. Protection: `RATE_LIMIT_ENABLED` and the per-endpoint `RATE_LIMIT_*_PER_MINUTE` (per user, per pod; answers `429` with `Retry-After`), `MAX_UPLOAD_BYTES` and the image pixel cap (`422 image_too_large`). Operations: `METRICS_SINK` (`log` or `cloudwatch`), `BATCH_DELETE_COOLDOWN_SECONDS`. Frontend build settings are in `frontend/.env.example`.

## Assumptions and limits

- One primary AWS region. Global speed comes from CloudFront and S3 Transfer Acceleration, not multi-region writes. Add a read replica if latency data calls for it.
- Bounding boxes only. One annotator and one reviewer per attempt, with a single image-level accept or reject.
- With too few users to satisfy the different-user rule, images wait in `submitted` until another reviewer is available.
- The Terraform and Kubernetes manifests were validated (`tofu validate`, plans against a mocked AWS, `kubectl kustomize`) but never applied to a real AWS account. Things that can only be proven there: real Cognito behaviour (including the `sub` filter the admin API uses to resolve usernames), CloudFront signed URLs and the CSP in a real browser, S3 Transfer Acceleration, NetworkPolicy enforcement by the VPC CNI, WAF rule behaviour (hence count mode first), EMF versus `PutMetricData` in a real account, and the GitHub environment approval gate.
- The admin user list is paged in whatever order Cognito returns, with no search yet. On a large pool, find a user by loading more pages.
- Rate limits are per pod, so the effective limit scales with replicas; IP-level protection is the WAF's job.
