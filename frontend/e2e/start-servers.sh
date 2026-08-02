#!/bin/sh
set -e

echo "[e2e] starting helper script"

# All required values must come from the environment. No defaults are used so
# that a missing value fails fast instead of silently running against the
# wrong database or with a predictable password.
if [ -z "${E2E_DATABASE_URL:-}" ] || [ -z "${E2E_ADMIN_EMAIL:-}" ] || [ -z "${E2E_ADMIN_NAME:-}" ] || [ -z "${E2E_ADMIN_PASSWORD:-}" ]; then
  echo "ERROR: E2E_DATABASE_URL, E2E_ADMIN_EMAIL, E2E_ADMIN_NAME, and E2E_ADMIN_PASSWORD must all be set (values omitted)."
  exit 1
fi

echo "[e2e] validating E2E_DATABASE_URL naming convention"
node ./e2e/validate-e2e-db-url.mjs || {
  echo "ERROR: E2E_DATABASE_URL failed validation (value omitted)."
  exit 2
}

echo "[e2e] creating initial admin using backend script"

cd ../backend
# Map E2E_* inputs to the names the backend script expects, only for this
# call. Secrets are passed through the environment only, never as command
# arguments, and are not printed on failure.
DATABASE_URL="$E2E_DATABASE_URL" \
INITIAL_ADMIN_EMAIL="$E2E_ADMIN_EMAIL" \
INITIAL_ADMIN_NAME="$E2E_ADMIN_NAME" \
INITIAL_ADMIN_PASSWORD="$E2E_ADMIN_PASSWORD" \
npm run create:initial-admin >/dev/null 2>&1 || {
  echo "ERROR: create:initial-admin failed (see backend logs locally; no secrets printed)."
  exit 3
}

echo "[e2e] starting backend (dev)"
DATABASE_URL="$E2E_DATABASE_URL" npm run dev &
BACKEND_PID=$!

# Ensure backend process is killed on exit
trap 'echo "[e2e] cleaning up"; kill "$BACKEND_PID" 2>/dev/null || true' EXIT INT TERM

echo "[e2e] waiting for backend to respond on http://localhost:3000/health"
count=0
until curl -sSf http://localhost:3000/health >/dev/null 2>&1; do
  count=$((count+1))
  if [ $count -gt 60 ]; then
    echo "ERROR: backend failed to start"
    kill $BACKEND_PID || true
    exit 4
  fi
  sleep 1
done

echo "[e2e] backend started"

echo "[e2e] starting frontend (dev)"
cd ../frontend
npm run dev
