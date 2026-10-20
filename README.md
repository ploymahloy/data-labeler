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
frontend/           React SPA, Vitest tests, Playwright e2e
deploy/k8s/         Kustomize: base, prod overlay, migration Job, ClusterSecretStore
infra/terraform/    VPC, EKS, RDS, S3, CloudFront, Cognito, ECR, IAM (IRSA, GitHub OIDC)
.github/workflows/  CI (lint, test, e2e, validate manifests and Terraform) and deploy
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
npx playwright install chromium
npm run e2e         # needs the compose stack running
```

The backend suite covers concurrent claims (25 users, 20 claimants for 5 images), reviewer independence including users who hold both roles, the rework and escalation loop, idempotent saves, dedupe, and auth. The Playwright suite covers two users draining a queue without overlap and the full upload, label, reject, rework, accept flow with independent reviewers.

## Deploy to AWS

1. **Infrastructure**
   ```bash
   cd infra/terraform
   cp terraform.tfvars.example terraform.tfvars   # set cognito_domain_prefix, optional domain and github_repository
   terraform init && terraform apply
   ```
   Configure a remote state backend in `versions.tf` first for real use. The apply also installs the AWS Load Balancer Controller and External Secrets Operator on the new cluster.
2. **Cluster wiring (once)**
   ```bash
   aws eks update-kubeconfig --name $(terraform output -raw cluster_name)
   kubectl apply -f deploy/k8s/cluster/secret-store.yaml
   ```
3. **Fill in the prod overlay** (`deploy/k8s/overlays/prod/kustomization.yaml`) and the ECR image name (`deploy/k8s/base/kustomization.yaml`) from `terraform output`: IRSA role ARN, Cognito pool and client IDs, bucket, CloudFront domain and key id, secret name, ACM certificate, API hostname. Create a DNS record for the API host pointing at the ALB.
4. **GitHub Actions**: set the repository variables listed at the top of `.github/workflows/deploy.yml` (`terraform output` has each value). Pushes to `main` that pass CI build and push the image, run the migration Job, roll out the API and reaper, sync the SPA to S3, and invalidate CloudFront.

Secrets (database URL, CloudFront signing key) are generated by Terraform into Secrets Manager and synced into the cluster by External Secrets. Pods use IRSA, so no AWS keys are stored.

Create users in the Cognito pool (admin-created only) and add them to the `admin`, `labeler`, and `reviewer` groups.

## Configuration

Backend settings are environment variables (see `backend/app/config.py`): `DATABASE_URL`, `AUTH_MODE` (`cognito` or `dev`), `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `S3_BUCKET`, `S3_USE_ACCELERATE`, `CLOUDFRONT_DOMAIN`, `CLOUDFRONT_KEY_PAIR_ID`, `CLOUDFRONT_PRIVATE_KEY_PEM`, `LEASE_MINUTES`, `MAX_REJECTIONS`, `REWORK_RESERVE_HOURS`, `DUPLICATE_IOU_THRESHOLD`, `CORS_ORIGINS`. Frontend build settings are in `frontend/.env.example`.

## Assumptions and limits

- One primary AWS region. Global speed comes from CloudFront and S3 Transfer Acceleration, not multi-region writes. Add a read replica if latency data calls for it.
- Bounding boxes only. One annotator and one reviewer per attempt, with a single image-level accept or reject.
- With too few users to satisfy the different-user rule, images wait in `submitted` until another reviewer is available.
- The Terraform and Kubernetes manifests were validated (`terraform validate`, `kubectl kustomize`) but not applied to a real AWS account.
