# Graph Report - data-labeler  (2026-10-20)

## Corpus Check
- 142 files · ~62,812 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 12 file(s) not represented in the graph (top: (none) 5, .example 3, .ini 1)

## Summary
- 1270 nodes · 3670 edges · 63 communities (44 shown, 19 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 303 edges (avg confidence: 0.93)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `98324254`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Test Fixtures & Seeding
- Storage & Migration Checks
- Rate Limiter
- Dev Login & Schemas
- Frontend API Client
- App Shell & Roles
- User Admin Tests
- Box Canvas
- Role-Based Access
- PKCE Auth Context
- ORM Models
- COCO Export Engine
- Settings
- Token Refresh
- Admin Router
- Cognito Admin
- Uploader
- E2E Concurrency Tests
- reaper.py
- json
- K8s API Deployment
- routers/reports.py
- get_settings
- JWT Auth
- observability.py
- Batches Router
- emit_cloudwatch_metrics
- JSON Logging Tests
- Frontend Tooling
- Dev Dependencies
- TypeScript Config
- run.py
- Auth Tests
- main.tsx
- Boto Cognito Adapter
- main.py
- Deploy Workflows
- Draft Stash
- Dev Stack Script
- Alarms Runbook
- Alembic Migrations
- Docker Compose Stack
- README Concepts
- Secrets & Terraform CI
- Rollback Runbook
- NPM Scripts
- Frontend Dependencies
- CI Workflow
- Smoke Test
- index.html SPA entry
- vite.config.ts
- data-labeler-backend

## God Nodes (most connected - your core abstractions)
1. `seed_batch()` - 76 edges
2. `Settings` - 64 edges
3. `CurrentUser` - 54 edges
4. `auth()` - 54 edges
5. `get_settings()` - 33 edges
6. `Role` - 33 edges
7. `Storage` - 33 edges
8. `Image` - 30 edges
9. `box()` - 30 edges
10. `ApiError` - 28 edges

## Surprising Connections (you probably didn't know these)
- `CI e2e job (Playwright + moto S3)` --semantically_similar_to--> `Shared backend env (dev auth, rate limit off)`  [INFERRED] [semantically similar]
  .github/workflows/ci.yml → docker-compose.yml
- `Run database migrations step (labeler-migrate Job)` --semantically_similar_to--> `migrate service (alembic upgrade head)`  [INFERRED] [semantically similar]
  .github/workflows/deploy-environment.yml → docker-compose.yml
- `Deploy smoke job` --semantically_similar_to--> `Production smoke test by hand`  [INFERRED] [semantically similar]
  .github/workflows/deploy-environment.yml → docs/runbooks/rollback.md
- `DB connection pool budget (labeler_db_pool_checked_out)` --cites--> `Data Labeler`  [AMBIGUOUS]
  docs/runbooks/alarms.md → README.md
- `CI k8s-manifests job (kustomize validation)` --references--> `Deploy to AWS procedure`  [INFERRED]
  .github/workflows/ci.yml → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Default-deny network policy set** — deploy_k8s_base_networkpolicies_default_deny, deploy_k8s_base_networkpolicies_api_ingress, deploy_k8s_base_networkpolicies_api_egress, deploy_k8s_base_networkpolicies_reaper_egress, deploy_k8s_base_networkpolicies_migrate_egress [EXTRACTED 1.00]
- **Workloads sharing labeler-api config, secrets and service account** — deploy_k8s_base_api_deployment_labeler_api_deployment, deploy_k8s_base_reaper_cronjob_labeler_reaper_cronjob, deploy_k8s_migrate_migrate_job_labeler_migrate_job, deploy_k8s_base_configmap_labeler_api_config, deploy_k8s_base_externalsecret_labeler_api_secrets [EXTRACTED 1.00]
- **Local compose startup chain (db, localstack, s3-init, migrate, api, reaper)** — docker_compose_db_service, docker_compose_localstack_service, docker_compose_s3_init_service, docker_compose_migrate_service, docker_compose_api_service, docker_compose_reaper_service [EXTRACTED 1.00]
- **Backward-compatible migrations enabling safe rollback** — docs_runbooks_rollback_expand_contract_migrations, github_workflows_ci_migration_safety_check, github_workflows_deploy_environment_migration_job_step, github_workflows_deploy_environment_auto_rollback_step, docs_runbooks_rollback_roll_back_api [EXTRACTED 1.00]
- **Staged deploy pipeline (staging -> smoke -> approval -> production)** — github_workflows_deploy_deploy_workflow, github_workflows_deploy_staging_job, github_workflows_deploy_production_approval_gate, github_workflows_deploy_production_job, github_workflows_deploy_environment_deploy_environment_workflow, github_workflows_deploy_environment_smoke_job [EXTRACTED 1.00]
- **API traffic path: ALB Ingress to Service to Deployment** — deploy_k8s_base_api_ingress_labeler_api_ingress, deploy_k8s_base_api_service_labeler_api_service, deploy_k8s_base_api_deployment_labeler_api_deployment, deploy_k8s_base_api_hpa_labeler_api_hpa, deploy_k8s_base_api_pdb_labeler_api_pdb [INFERRED 0.85]

## Communities (63 total, 19 thin omitted)

### Community 0 - "Test Fixtures & Seeding"
Cohesion: 0.08
Nodes (79): run_once(), auth(), box(), seed_batch(), claim(), _label_and_accept(), save(), test_boxes_can_swap_positions_in_one_save() (+71 more)

### Community 1 - "Storage & Migration Checks"
Cohesion: 0.06
Nodes (37): _alembic(), check_source(), check_versions(), diff_snapshots(), Finding, _is_const(), _kwarg(), main() (+29 more)

### Community 2 - "Rate Limiter"
Cohesion: 0.10
Nodes (27): RateLimiter, create_batch(), png_bytes(), upload_images(), FakeClock, limits_on(), test_bucket_allows_burst_then_refills_over_time(), test_claim_endpoint_returns_429_with_retry_after() (+19 more)

### Community 3 - "Dev Login & Schemas"
Cohesion: 0.11
Nodes (17): mint_dev_token(), dev_login(), AdminRolesUpdate, AdminUserCreate, AnnotationOut, BatchCreate, DevLoginRequest, DevLoginResponse (+9 more)

### Community 4 - "Frontend API Client"
Cohesion: 0.09
Nodes (36): api, ApiError, isApiError(), isUnauthorized(), makeApiError(), onUnauthorized(), parseError(), request() (+28 more)

### Community 5 - "App Shell & Roles"
Cohesion: 0.21
Nodes (17): App(), queryClient, Probe(), useAuth(), consumeReturnPath(), peekReturnPath(), Layout(), AdminUsersPage() (+9 more)

### Community 6 - "User Admin Tests"
Cohesion: 0.15
Nodes (23): get_cognito_admin(), sub_of(), boto_admin(), fresh_fake(), invite(), make_admins(), other_admin(), test_boto_implementation_full_lifecycle_through_the_api() (+15 more)

### Community 7 - "Box Canvas"
Cohesion: 0.11
Nodes (35): isLeaseLost(), Rect, BoxCanvas(), Drag, labelColor(), pct(), Props, rectStyle() (+27 more)

### Community 8 - "Role-Based Access"
Cohesion: 0.21
Nodes (16): CurrentUser, require_role(), dependency(), Role, get_image(), heartbeat(), next_for_review(), next_image() (+8 more)

### Community 9 - "PKCE Auth Context"
Cohesion: 0.14
Nodes (26): RFC-7636, setUnauthorizedHandler(), b64url(), token(), AuthContext, AuthProvider(), AuthState, cognito() (+18 more)

### Community 10 - "ORM Models"
Cohesion: 0.16
Nodes (12): not_found(), Annotation, Base, Batch, Decision, ImageSkip, _in_list(), Label (+4 more)

### Community 11 - "COCO Export Engine"
Cohesion: 0.15
Nodes (15): get_export_engine(), open_coco_export(), Sql, annotation_multiset(), reference(), seed_export_batch(), stream_peak(), streamed() (+7 more)

### Community 12 - "Settings"
Cohesion: 0.05
Nodes (53): Settings, ApiError, forbidden(), lease_lost(), Image, ImageStatus, ImageSubmission, LeaseKind (+45 more)

### Community 13 - "Token Refresh"
Cohesion: 0.14
Nodes (21): refreshTokens(), doRefresh(), RefreshOutcome, refreshSession(), current, getSession(), listeners, saveSession() (+13 more)

### Community 14 - "Admin Router"
Cohesion: 0.23
Nodes (14): change_roles(), disable_user(), enable_user(), invite_user(), list_users(), AdminUserOut, AdminUsersPage, _call() (+6 more)

### Community 15 - "Cognito Admin"
Cohesion: 0.14
Nodes (4): AdminUser, CognitoAdmin, FakeCognitoAdmin, UserPage

### Community 16 - "Uploader"
Cohesion: 0.14
Nodes (20): CompleteResult, UploadTicket, ACCEPTED_TYPES, chunk(), FINAL_STATUSES, MAX_BYTES, messageOf(), Options (+12 more)

### Community 17 - "E2E Concurrency Tests"
Cohesion: 0.13
Nodes (19): Claimed, API_URL, at(), authHeaders(), canvasBounds(), chunk(), devToken(), drawBox() (+11 more)

### Community 18 - "reaper.py"
Cohesion: 0.23
Nodes (9): configure_logging(), publish_cloudwatch_metrics(), collect_stats(), main(), report(), test_cloudwatch_sink_publishes_heartbeat_with_put_metric_data(), test_publish_failure_is_reported_not_raised(), test_reaper_failure_exits_nonzero_and_emits_failure_metric() (+1 more)

### Community 20 - "K8s API Deployment"
Cohesion: 0.21
Nodes (22): labeler-api Deployment, labeler-api HorizontalPodAutoscaler, labeler-api Ingress (ALB), labeler-api PodDisruptionBudget, labeler-api Service (ClusterIP), labeler-api-config ConfigMap, aws-secrets-manager ClusterSecretStore, labeler-api-secrets ExternalSecret (+14 more)

### Community 21 - "routers/reports.py"
Cohesion: 0.19
Nodes (4): rate_limit(), export_batch(), review_stats(), ReviewStats

### Community 22 - "get_settings"
Cohesion: 0.10
Nodes (12): do_run_migrations(), run_migrations_offline(), run_migrations_online(), get_settings(), build_connect_args(), get_engine(), get_session(), get_sessionmaker() (+4 more)

### Community 23 - "JWT Auth"
Cohesion: 0.13
Nodes (11): claims_to_user(), cognito_issuer(), current_user(), decode_token(), _remote_jwks_client(), _signing_key(), _static_jwks(), _sync_user() (+3 more)

### Community 25 - "Batches Router"
Cohesion: 0.18
Nodes (20): archive_batch(), complete_uploads(), _counts(), create_batch(), delete_batch(), get_batch(), list_batches(), list_escalated() (+12 more)

### Community 27 - "JSON Logging Tests"
Cohesion: 0.22
Nodes (9): JsonFormatter, json_logs(), test_access_log_is_structured_with_route_user_and_latency(), test_health_probes_are_not_logged(), test_metrics_are_not_served_through_the_load_balancer(), test_metrics_expose_http_domain_and_pool_series(), test_queue_gauges_track_submitted_images_and_expired_leases(), test_request_id_is_generated_and_echoed() (+1 more)

### Community 28 - "Frontend Tooling"
Cohesion: 0.13
Nodes (16): name, private, type, version, eslint, @eslint/js, eslint-plugin-react-hooks, globals (+8 more)

### Community 29 - "Dev Dependencies"
Cohesion: 0.11
Nodes (18): devDependencies, eslint, @eslint/js, eslint-plugin-react-hooks, globals, jsdom, @playwright/test, @testing-library/jest-dom (+10 more)

### Community 30 - "TypeScript Config"
Cohesion: 0.11
Nodes (17): compilerOptions, isolatedModules, jsx, lib, module, moduleResolution, noEmit, noFallthroughCasesInSwitch (+9 more)

### Community 31 - "run.py"
Cohesion: 0.08
Nodes (10): Config, LoadTest, put(), main(), parse_args(), percentile(), png(), Stats (+2 more)

### Community 32 - "Auth Tests"
Cohesion: 0.25
Nodes (14): make_token(), test_access_tokens_are_rejected_id_token_required(), test_dev_login_is_not_mounted_in_cognito_mode(), test_expired_token_is_rejected(), test_expiry_returns_a_401_the_spa_can_recover_from(), test_garbage_token_is_401(), test_hs256_tokens_are_not_accepted_outside_dev_mode(), test_missing_token_is_401() (+6 more)

### Community 34 - "Boto Cognito Adapter"
Cohesion: 0.15
Nodes (4): _attrs(), BotoCognitoAdmin, UserExistsError, UserNotFoundError

### Community 36 - "main.py"
Cohesion: 0.14
Nodes (6): create_app(), metrics(), readyz(), queue_stats(), render_metrics(), RequestContextMiddleware

### Community 37 - "Deploy Workflows"
Cohesion: 0.23
Nodes (12): Production smoke test by hand, Roll back the SPA procedure, Deploy Workflow, Deploy api job (build image, migrate, roll out API and reaper), Deploy environment reusable workflow, Per-GitHub-environment deploy variables, S3 cache policy (immutable assets, no-cache index.html), Deploy smoke job (+4 more)

### Community 38 - "Draft Stash"
Cohesion: 0.27
Nodes (9): Box, Stash, stashCanvasDraft(), stashDraft(), takeDraft(), box, CanvasState, isDirty() (+1 more)

### Community 39 - "Dev Stack Script"
Cohesion: 0.15
Nodes (12): AUTH_MODE, AWS_ACCESS_KEY_ID, AWS_REGION, AWS_SECRET_ACCESS_KEY, BATCH_DELETE_COOLDOWN_SECONDS, CORS_ORIGINS, DATABASE_URL, DEV_AUTH_SECRET (+4 more)

### Community 40 - "Alarms Runbook"
Cohesion: 0.24
Nodes (11): Alarm runbook, Alarms SNS topic, api-5xx-rate alarm, api-unhealthy-targets alarm, DB connection pool budget (labeler_db_pool_checked_out), RDS alarms (cpu, connections, free-storage, freeable-memory), reaper-failures alarm, reaper-stalled alarm (+3 more)

### Community 42 - "Alembic Migrations"
Cohesion: 0.29
Nodes (4): downgrade(), upgrade(), upgrade(), upgrade()

### Community 44 - "Docker Compose Stack"
Cohesion: 0.38
Nodes (10): api service, Shared backend env (dev auth, rate limit off), docker-compose local stack, db service (postgres:16-alpine), localstack service (S3), migrate service (alembic upgrade head), reaper service (30s loop), s3-init service (bucket + CORS) (+2 more)

### Community 45 - "README Concepts"
Cohesion: 0.24
Nodes (10): review-backlog-age alarm, User-reported symptoms (session expired, 429, 503 db_busy, image_too_large), Backend environment configuration, Data Labeler, Duplicate image/box/label prevention, Idempotent annotation saves, Image lifecycle state machine, Postgres lease claim (one user per image) (+2 more)

### Community 48 - "Secrets & Terraform CI"
Cohesion: 0.28
Nodes (9): CI k8s-manifests job (kustomize validation), CI terraform job (fmt, validate, add-ons), secret-store Helm chart, ClusterSecretStore aws-secrets-manager, secret-store values (region us-east-1), Deploy to AWS procedure, External Secrets + IRSA secret delivery, Terraform add-ons stack (+1 more)

### Community 49 - "Rollback Runbook"
Cohesion: 0.29
Nodes (6): Failed migration procedure (pause, PITR restore, forward fix), RDS point-in-time restore, Roll back the API procedure, Rollback runbook, Migration safety check (check_migrations static + roundtrip), Run database migrations step (labeler-migrate Job)

### Community 50 - "NPM Scripts"
Cohesion: 0.25
Nodes (8): scripts, build, dev, e2e, lint, preview, test, typecheck

### Community 51 - "Frontend Dependencies"
Cohesion: 0.33
Nodes (6): dependencies, react, react-dom, react-router-dom, @tanstack/react-query, zustand

### Community 52 - "CI Workflow"
Cohesion: 0.47
Nodes (6): CI backend job (ruff, migration safety, pytest), CI Workflow, CI docker build job, CI e2e job (Playwright + moto S3), CI frontend job (lint, typecheck, test, build), Backend and frontend test suites

## Ambiguous Edges - Review These
- `DB connection pool budget (labeler_db_pool_checked_out)` → `Data Labeler`  [AMBIGUOUS]
  docs/runbooks/alarms.md · relation: cites

## Knowledge Gaps
- **34 isolated node(s):** `data-labeler-backend`, `@types/node`, `@types/react`, `@types/react-dom`, `eslint` (+29 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 347 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **19 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Settings` connect `Settings` to `Test Fixtures & Seeding`, `Storage & Migration Checks`, `Boto Cognito Adapter`, `Dev Login & Schemas`, `User Admin Tests`, `Role-Based Access`, `reaper.py`, `get_settings`, `JWT Auth`, `observability.py`, `Batches Router`, `run.py`?**
  _High betweenness centrality (0.139) - this node is a cross-community bridge._
- **Are the 36 inferred relationships involving `Settings` (e.g. with `cognito_issuer()` and `current_user()`) actually correct?**
  _`Settings` has 36 INFERRED edges - model-reasoned connections that need verification._
- **What connects `data-labeler-backend`, `@types/node`, `@types/react` to the rest of the system?**
  _34 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Test Fixtures & Seeding` be split into smaller, more focused modules?**
  _Cohesion score 0.07663366336633663 - nodes in this community are weakly interconnected._
- **Why does `make_token()` connect `Auth Tests` to `Test Fixtures & Seeding`, `PKCE Auth Context`, `Rate Limiter`, `User Admin Tests`?**
  _High betweenness centrality (0.121) - this node is a cross-community bridge._
- **Are the 40 inferred relationships involving `CurrentUser` (e.g. with `Role` and `rate_limit()`) actually correct?**
  _`CurrentUser` has 40 INFERRED edges - model-reasoned connections that need verification._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `DB connection pool budget (labeler_db_pool_checked_out)` and `Data Labeler`?**
  _Edge tagged AMBIGUOUS (relation: cites) - confidence is low._