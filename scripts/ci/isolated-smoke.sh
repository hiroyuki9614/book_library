#!/usr/bin/env bash
set -Eeuo pipefail

compose_file="${COMPOSE_FILE:-compose.yaml}"
project="${COMPOSE_PROJECT_NAME:-belib-ci-${GITHUB_RUN_ID:-local}-$$-${RANDOM}}"
api_url="${API_URL:-http://127.0.0.1:8080}"
ui_url="${UI_URL:-http://127.0.0.1:8080}"

loopback_url() {
  [[ "$1" =~ ^https?://(127\.0\.0\.1|localhost)(:[0-9]+)?(/.*)?$ ]]
}
if ! loopback_url "$api_url" || ! loopback_url "$ui_url"; then
  echo "API_URL and UI_URL must each be loopback-only URLs" >&2
  exit 1
fi

cleanup() {
  docker compose --project-name "$project" --file "$compose_file" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

export COMPOSE_PROJECT_NAME="$project"
export POSTGRES_USER="ci"
export POSTGRES_PASSWORD="ci-only-password"
export POSTGRES_DB="ci_smoke"
export BETTER_AUTH_SECRET="ci-only-secret-${GITHUB_RUN_ID:-local}"
export ENVIRONMENT="test"
export FRONTEND_URL="$ui_url"
export BETTER_AUTH_URL="$api_url"
export VITE_API_BASE_URL="$api_url"
export BOOK_FILE_STORAGE_ROOT="/tmp/belib-ci-storage"

docker compose --project-name "$project" --file "$compose_file" up --detach db
docker compose --project-name "$project" --file "$compose_file" --profile migration run --rm migrate
docker compose --project-name "$project" --file "$compose_file" up --detach backend frontend nginx

for attempt in {1..60}; do
  health_body="$(mktemp)"
  if curl --silent --show-error --fail --output "$health_body" "$api_url/health" && grep -Eiq '"status"[[:space:]]*:[[:space:]]*"ok"' "$health_body" && grep -Eiq '"service"[[:space:]]*:[[:space:]]*"belib-api"' "$health_body"; then
    echo "Health JSON: $(cat "$health_body")"
    rm -f "$health_body"
    break
  fi
  rm -f "$health_body"
  if [[ "$attempt" == 60 ]]; then
    echo "API health smoke failed" >&2
    exit 1
  fi
  sleep 2
done

ui_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "$ui_url/")"
[[ "$ui_status" =~ ^2[0-9][0-9]$ ]] || { echo "UI smoke failed with HTTP $ui_status" >&2; exit 1; }
echo "UI reached $ui_url/ with HTTP $ui_status"

protected_body="$(mktemp)"
protected_status="$(curl --silent --show-error --output "$protected_body" --write-out '%{http_code}' "$api_url/api/v1/books")"
if [[ "$protected_status" != "401" && "$protected_status" != "403" ]]; then
  echo "Protected API was not rejected unauthenticated: HTTP $protected_status; body: $(cat "$protected_body")" >&2
  rm -f "$protected_body"
  exit 1
fi
echo "Unauthenticated protected API rejection: HTTP $protected_status; body: $(cat "$protected_body")"
rm -f "$protected_body"

echo "Isolated API and UI smoke checks passed for Compose project $project"
