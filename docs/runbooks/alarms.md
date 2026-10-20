# Alarm runbook

All alarms publish to the `<name>-<environment>-alarms` SNS topic (Terraform output
`alarm_topic_arn`; email via the `alarm_email` variable). Logs are JSON with a `request_id`;
every response carries `X-Request-ID`, so a user report with that header leads straight to the log lines.

| Alarm | Meaning | First steps |
| --- | --- | --- |
| `*-reaper-stalled` | No heartbeat for 10 minutes. Expired leases are not returned to the queue and every other reaper-based alarm is blind. | `kubectl -n labeler get cronjob,job,pod -l app.kubernetes.io/name=labeler-reaper`. Look for `ImagePullBackOff`, a failing network policy (egress to 5432/443), or IRSA errors in the pod log (`PutMetricData` denied). If the job runs but metrics are missing, check the log for `metrics_publish_failed`. |
| `*-reaper-failures` | The job ran and crashed (database unreachable, query error). | `kubectl -n labeler logs job/<latest>`; see the `reaper_failed` log line. Usually the same cause as `*-rds-*` alarms. |
| `*-review-backlog-age` | The oldest `submitted` image has waited longer than `submitted_age_alarm_seconds` (default 24 h). | Not an outage. Too few reviewers, or the only available reviewers are the annotators (the different-user rule). Add reviewers (Admin → Users) or check `/metrics` `labeler_submitted_images`. |
| `*-api-5xx-rate` | More than 5 % of requests fail with 5xx. | Check recent deploys (`kubectl rollout history`); roll back if correlated (see rollback.md). `503 db_busy` means the connection pool is exhausted, see below. |
| `*-api-unhealthy-targets` | Pods failing `/readyz` (database unreachable or pool saturated). | `kubectl -n labeler describe pod`; check RDS alarms and network policies. |
| `*-rds-cpu` | CPU above 80 % for 15 minutes. | Performance Insights → top SQL. The queue queries use partial indexes (`ix_images_submitted`, `ix_images_lease_expiry`); a missing index or a large export are the usual suspects. |
| `*-rds-connections` | Connections near `max_connections`. | Compare `sum(labeler_db_pool_checked_out)` from `/metrics` with the budget in the README ("Database connections"). Lower `MAX_API_PODS`/raise the instance class, or investigate a leaked connection. |
| `*-rds-free-storage` | Under 20 % free of the allocated size. | Storage autoscaling is on up to 4× `db_allocated_storage`. Check what grew (images table rows, annotations). Raise `db_allocated_storage` if it is organic. |
| `*-rds-freeable-memory` | Under 256 MiB freeable. | Usually follows high connection counts; same steps as `*-rds-connections`. |

## Symptoms users report

- **"Session expired" while labeling**: unsaved boxes are stashed in the browser and restored after
  sign-in. If Cognito is unreachable the app keeps the session and retries every 15 s.
- **HTTP 429 `rate_limited`**: per-user token bucket; the response has `Retry-After`. Raise
  `RATE_LIMIT_*` only if legitimate usage hits it.
- **HTTP 503 `db_busy`**: pool exhausted; the client retries after `Retry-After: 2`.
- **Upload says `image_too_large`**: the image exceeds `MAX_IMAGE_PIXELS` (100 MP) or
  `MAX_IMAGE_SIDE` (20 000 px).

## WAF

Tune with `waf_block_mode = false` (count only) and review sampled requests in the web ACL, then
set it to `true`. The `AWSManagedRulesCommonRuleSet` body-size rule is overridden to count
because annotation saves can exceed 8 KB.
