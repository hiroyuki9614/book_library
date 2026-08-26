#!/bin/sh
set -eu

BACKEND_PID=''
FRONTEND_PID=''
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

cleanup() {
  if [ -n "$FRONTEND_PID" ]; then
    kill "$FRONTEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi

  if [ -n "$BACKEND_PID" ]; then
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
}

wait_for_url() {
  url=$1
  pid=$2
  server_name=$3
  timeout=$4
  elapsed=0

  until curl -fsS "$url" >/dev/null 2>&1; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "ERROR: $server_name exited before becoming ready."
      exit 1
    fi

    if [ "$elapsed" -ge "$timeout" ]; then
      echo "ERROR: $server_name readiness timeout."
      exit 1
    fi

    sleep 1
    elapsed=$((elapsed + 1))
  done
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -z "${E2E_DATABASE_URL:-}" ] || [ -z "${E2E_ADMIN_EMAIL:-}" ] || [ -z "${E2E_ADMIN_NAME:-}" ] || [ -z "${E2E_ADMIN_PASSWORD:-}" ] || [ -z "${E2E_MVP_PASSWORD:-}" ] || [ -z "${E2E_BOOK_FILE_STORAGE_ROOT:-}" ] || [ -z "${E2E_MVP_METADATA_PATH:-}" ]; then
  echo 'ERROR: E2E_DATABASE_URL, admin credentials, E2E_MVP_PASSWORD, E2E_BOOK_FILE_STORAGE_ROOT, and E2E_MVP_METADATA_PATH are required (values omitted).'
  exit 1
fi

node "$SCRIPT_DIR/validate-e2e-db-url.mjs"

BETTER_AUTH_SECRET=$(openssl rand -hex 32)

cd ../backend
DATABASE_URL="$E2E_DATABASE_URL" npx prisma db push --accept-data-loss >/dev/null 2>&1 || {
  echo 'ERROR: isolated E2E database schema synchronization failed (values omitted).'
  exit 1
}
npm run build >/dev/null 2>&1 || {
  echo 'ERROR: backend build failed before E2E startup (values omitted).'
  exit 1
}
DATABASE_URL="$E2E_DATABASE_URL" \
INITIAL_ADMIN_EMAIL="$E2E_ADMIN_EMAIL" \
INITIAL_ADMIN_NAME="$E2E_ADMIN_NAME" \
INITIAL_ADMIN_PASSWORD="$E2E_ADMIN_PASSWORD" \
npm run create:initial-admin >/dev/null 2>&1 || {
  echo 'ERROR: initial admin setup failed (values omitted).'
  exit 1
}

BETTER_AUTH_SECRET="$BETTER_AUTH_SECRET" DATABASE_URL="$E2E_DATABASE_URL" BOOK_FILE_STORAGE_ROOT="$E2E_BOOK_FILE_STORAGE_ROOT" node "$SCRIPT_DIR/../../backend/dist/src/index.js" &
BACKEND_PID=$!

wait_for_url 'http://localhost:3000/health' "$BACKEND_PID" 'backend' 60

E2E_DATABASE_URL="$E2E_DATABASE_URL" \
E2E_API_BASE_URL='http://localhost:3000' \
E2E_MVP_PASSWORD="$E2E_MVP_PASSWORD" \
E2E_BOOK_FILE_STORAGE_ROOT="$E2E_BOOK_FILE_STORAGE_ROOT" \
E2E_MVP_METADATA_PATH="$E2E_MVP_METADATA_PATH" \
MVP_BOOK_ID=$(npm run --silent prepare:real-e2e-fixture 2>/dev/null) || {
  echo 'ERROR: real MVP fixture setup failed (values omitted).'
  exit 1
}

case "$MVP_BOOK_ID" in
  ''|*[!0-9]*)
    echo 'ERROR: real MVP fixture returned an invalid book id.'
    exit 1
    ;;
esac
export E2E_MVP_BOOK_ID="$MVP_BOOK_ID"

cd ../frontend
VITE_API_BASE_URL='http://localhost:3000' npm run dev &
FRONTEND_PID=$!

wait_for_url 'http://localhost:5173' "$FRONTEND_PID" 'frontend' 60

while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done

echo 'ERROR: an E2E server exited unexpectedly.'
exit 1
