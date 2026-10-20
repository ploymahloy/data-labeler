#!/usr/bin/env bash
# Post-deploy smoke test. Usage: scripts/smoke-test.sh API_URL [WEB_URL]
#
#   EXPECT_METRICS_BLOCKED=1   /metrics must be 404 (true behind the ALB; false on a local stack)
#   WAIT_SECONDS=300           how long to wait for /readyz
set -euo pipefail

api="${1:?usage: smoke-test.sh API_URL [WEB_URL]}"
web="${2:-}"
api="${api%/}"
web="${web%/}"
wait_seconds="${WAIT_SECONDS:-300}"
fail=0

check() { # name expected actual
  if [[ "$2" == "$3" ]]; then echo "ok    $1 ($3)"; else echo "FAIL  $1: expected $2, got $3"; fail=1; fi
}

status() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@" || echo 000; }

echo "Waiting for $api/readyz (up to ${wait_seconds}s)"
deadline=$((SECONDS + wait_seconds))
until [[ "$(status "$api/readyz")" == "200" ]]; do
  if ((SECONDS >= deadline)); then echo "FAIL  /readyz never returned 200"; exit 1; fi
  sleep 5
done
check "api /readyz" 200 200
check "api /healthz" 200 "$(status "$api/healthz")"
check "api rejects anonymous requests" 401 "$(status "$api/batches")"

if [[ "${EXPECT_METRICS_BLOCKED:-1}" == "1" ]]; then
  check "api /metrics not public" 404 "$(status "$api/metrics")"
fi

if [[ -n "$web" ]]; then
  check "web index" 200 "$(status "$web/")"
  headers="$(curl -sI --max-time 10 "$web/" | tr -d '\r' | tr 'A-Z' 'a-z')"
  for want in "content-security-policy:.*frame-ancestors 'none'" "strict-transport-security:" "x-content-type-options: nosniff"; do
    if grep -Eq "$want" <<<"$headers"; then echo "ok    web header $want"; else echo "FAIL  web header missing: $want"; fail=1; fi
  done
  check "web deep link serves the SPA" 200 "$(status "$web/batches/does-not-exist")"
fi

exit "$fail"
