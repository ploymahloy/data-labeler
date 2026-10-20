# Rollback runbook

Deploys go staging → smoke test → manual approval → production
(`.github/workflows/deploy.yml`). Rolling deploys run the old and new API side by side, and the
migration Job runs *before* the new API starts. That ordering is what makes rollback safe, so
every migration has to follow expand/contract.

## Migration rules

1. **Expand** (release N): add nullable columns, columns with a default, new tables, new indexes.
   Old code keeps working. CI (`scripts/check_migrations.py static`) rejects anything else.
2. **Switch** (release N+1): stop reading and writing the old column.
3. **Contract** (release N+2 or later): drop the old column. Put
   `# expand-contract: approved` and the release that stopped using it in the migration file.
   Only add this marker once the previous release no longer touches the thing being dropped.

CI also runs upgrade → downgrade → upgrade on a scratch database and `alembic check`
(models and migrations agree).

## Roll back the API

When to roll back: error rate alarm after a deploy, `/readyz` failing, a bug in the new release.

```bash
aws eks update-kubeconfig --name <cluster> --region <region>
kubectl -n labeler rollout history deployment/labeler-api
kubectl -n labeler rollout undo deployment/labeler-api            # previous revision
kubectl -n labeler rollout status deployment/labeler-api
```

The reaper CronJob uses the same image. Point it at the previous tag too:

```bash
PREV=$(kubectl -n labeler get deployment labeler-api -o jsonpath='{.spec.template.spec.containers[0].image}')
kubectl -n labeler set image cronjob/labeler-reaper reaper="$PREV"
```

Do **not** downgrade the database for an ordinary rollback. The previous release works with the
newer schema because the new migration was only an expand step.

The deploy workflow already runs `rollout undo` when the API rollout itself fails.

## Roll back the SPA

The SPA bucket is not versioned by the deploy. Redeploy the previous commit through the workflow
(Actions → Deploy → Run workflow on the old SHA), or rebuild locally and sync:

```bash
git checkout <good-sha> && cd frontend && npm ci && npm run build   # with the VITE_* variables
aws s3 sync dist s3://<web-bucket> --delete --exclude index.html \
  --cache-control "public,max-age=31536000,immutable"
aws s3 cp dist/index.html s3://<web-bucket>/index.html --cache-control no-cache
aws cloudfront create-invalidation --distribution-id <id> --paths "/index.html" "/"
```

## A migration went wrong

1. Stop the rollout: `kubectl -n labeler rollout pause deployment/labeler-api`.
2. Read the Job log: `kubectl -n labeler logs job/labeler-migrate`. Alembic runs each migration in
   a transaction, so a failed migration leaves the schema at the previous revision.
3. If a migration *succeeded* but was destructive and approved by mistake, restore from the RDS
   automated backup (point-in-time restore to a new instance, 14 days retained), then repoint
   `DATABASE_URL` in the Secrets Manager secret and restart the pods. This is the only path that
   brings data back, so test the restore procedure in staging before you need it.
4. A forward fix (a new expand migration) is almost always faster than a `downgrade`.

## Production smoke test by hand

```bash
scripts/smoke-test.sh https://api.example.com https://labeler.example.com
```
