#!/usr/bin/env bash
# Run the API against a local Postgres + S3-compatible server WITHOUT docker.
#
# Prereqs (once):  cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
# Needs: a Postgres you can reach (DATABASE_URL) and an S3 endpoint (S3_ENDPOINT_URL; e.g.
#        `backend/.venv/bin/moto_server -p 4566` with the bucket created).
# With docker available, prefer `docker compose up --build`.
set -euo pipefail

cd "$(dirname "$0")/../backend"

export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://postgres@localhost:5432/labeler}"
export AUTH_MODE="${AUTH_MODE:-dev}"
export DEV_AUTH_SECRET="${DEV_AUTH_SECRET:-local-dev-secret-change-me-0123456789abcdef}"
export S3_BUCKET="${S3_BUCKET:-labeler-images}"
export S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-http://127.0.0.1:4566}"
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export CORS_ORIGINS="${CORS_ORIGINS:-[\"http://localhost:5173\"]}"

.venv/bin/alembic upgrade head
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
