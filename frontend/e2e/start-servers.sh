#!/bin/sh
set -e

echo "[e2e] starting helper script"

if [ -z "${DATABASE_URL}" ]; then
  echo "ERROR: DATABASE_URL must be set to an E2E database (contains 'e2e' or 'test')."
  exit 1
fi

echo "[e2e] checking DATABASE_URL (masked)"
# Basic check without printing the full value to logs
echo "${DATABASE_URL}" | grep -E "e2e|test" >/dev/null || {
  echo "ERROR: DATABASE_URL does not look like an E2E/test database (value omitted)."
  exit 2
}

INITIAL_ADMIN_EMAIL=${INITIAL_ADMIN_EMAIL:-e2e-admin@example.test}
INITIAL_ADMIN_NAME=${INITIAL_ADMIN_NAME:-"E2E Admin"}
INITIAL_ADMIN_PASSWORD=${INITIAL_ADMIN_PASSWORD:-E2eAdminPass123!}

echo "[e2e] creating initial admin using backend script"

cd ../backend
# Run create:initial-admin but do not leak secrets to logs. Rely on exit code.
INITIAL_ADMIN_EMAIL="$INITIAL_ADMIN_EMAIL" INITIAL_ADMIN_NAME="$INITIAL_ADMIN_NAME" INITIAL_ADMIN_PASSWORD="$INITIAL_ADMIN_PASSWORD" npm run create:initial-admin >/dev/null 2>&1 || {
  echo "ERROR: create:initial-admin failed (see backend logs locally)."
  exit 3
}

echo "[e2e] starting backend (dev)"
npm run dev &
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
