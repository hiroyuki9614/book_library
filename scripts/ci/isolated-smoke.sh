#!/usr/bin/env bash
set -Eeuo pipefail

recovery_rehearsal="${BELIB_CI_RECOVERY_REHEARSAL:-}"
if [[ -z "$recovery_rehearsal" ]]; then
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
elif [[ "$recovery_rehearsal" == "github-hosted-v1" ]]; then
  if [[ "${RUNNER_ENVIRONMENT:-}" != "github-hosted" || "${GITHUB_ACTIONS:-}" != "true" \
    || ! "${GITHUB_RUN_ID:-}" =~ ^[0-9]+$ || ! "${GITHUB_RUN_ATTEMPT:-}" =~ ^[0-9]+$ ]]; then
    echo "Recovery rehearsal requires an independent GitHub-hosted Actions VM" >&2
    exit 1
  fi
  if [[ -n "${COMPOSE_FILE:-}" || -n "${COMPOSE_PROJECT_NAME:-}" || -n "${API_URL:-}" || -n "${UI_URL:-}" ]]; then
    echo "Recovery rehearsal does not accept caller-selected Compose paths, projects, or URLs" >&2
    exit 1
  fi
  for docker_override in DOCKER_HOST DOCKER_CONTEXT DOCKER_TLS DOCKER_TLS_VERIFY DOCKER_CERT_PATH DOCKER_CONFIG; do
    if [[ -n "${!docker_override:-}" ]]; then
      echo "Recovery rehearsal rejects Docker endpoint override environment" >&2
      exit 1
    fi
  done
  if ! docker_endpoint="$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null)" \
    || [[ "$docker_endpoint" != "unix:///var/run/docker.sock" ]]; then
    echo "Refusing recovery rehearsal: Docker context is not the runner-local socket" >&2
    exit 1
  fi
  compose_file="compose.yaml"
  project="belib-recovery-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
  network="${project}_default"
  api_url="http://127.0.0.1:8080"
  ui_url="http://127.0.0.1:8080"

  if ! running_containers="$(docker ps -q --no-trunc)"; then
    echo "Refusing recovery rehearsal: cannot verify the runner container inventory" >&2
    exit 1
  fi
  if [[ -n "$running_containers" ]]; then
    echo "Refusing recovery rehearsal: unexpected containers are already running" >&2
    exit 1
  fi
  if ! production_containers="$(docker ps -aq --filter label=com.docker.compose.project=belib-production)"; then
    echo "Refusing recovery rehearsal: cannot verify the production project inventory" >&2
    exit 1
  fi
  if [[ -n "$production_containers" ]]; then
    echo "Refusing recovery rehearsal: production project containers exist on this runner" >&2
    exit 1
  fi
  if ! project_volumes="$(docker volume ls -q --filter label=com.docker.compose.project="$project")"; then
    echo "Refusing recovery rehearsal: cannot verify synthetic project volumes" >&2
    exit 1
  fi
  if [[ -n "$project_volumes" ]]; then
    echo "Refusing recovery rehearsal: this CI project already has volumes" >&2
    exit 1
  fi
  if ! project_networks="$(docker network ls -q --filter name="^$network$")"; then
    echo "Refusing recovery rehearsal: cannot verify synthetic project network" >&2
    exit 1
  fi
  if [[ -n "$project_networks" ]]; then
    echo "Refusing recovery rehearsal: this CI project already has a network" >&2
    exit 1
  fi
else
  echo "Unknown isolated recovery rehearsal mode" >&2
  exit 1
fi

cleanup() {
  docker compose --project-name "$project" --file "$compose_file" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ "$recovery_rehearsal" == "github-hosted-v1" ]]; then
  docker network create \
    --label "com.docker.compose.project=$project" \
    --label com.docker.compose.network=default \
    "$network" >/dev/null
  network_identity="$(docker network inspect --format '{{.Name}}|{{.Internal}}|{{index .Labels "com.docker.compose.project"}}|{{index .Labels "com.docker.compose.network"}}' "$network")"
  [[ "$network_identity" == "$network|false|$project|default" ]] || {
    echo "Synthetic Compose network is not the project-owned bridge" >&2
    exit 1
  }
fi

export COMPOSE_PROJECT_NAME="$project"
export POSTGRES_USER="ci"
export POSTGRES_PASSWORD="ci-only-password"
export POSTGRES_DB="ci_smoke"
export BETTER_AUTH_SECRET="ci-only-secret-${GITHUB_RUN_ID:-local}"
export ENVIRONMENT="test"
export FRONTEND_URL="$ui_url"
export BETTER_AUTH_URL="$api_url"
export VITE_API_BASE_URL="$api_url"
if [[ "$recovery_rehearsal" == "github-hosted-v1" ]]; then
  unset R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET_NAME R2_ENDPOINT
fi
export BOOK_FILE_STORAGE_DRIVER="local"
export BOOK_FILE_STORAGE_ROOT="/tmp/belib-ci-storage"

docker compose --project-name "$project" --file "$compose_file" up --detach db
docker compose --project-name "$project" --file "$compose_file" --profile migration run --rm migrate
docker compose --project-name "$project" --file "$compose_file" up --detach backend frontend nginx

# Use the running runtime image, its existing DB network and storage mount.
backend_id="$(docker compose --project-name "$project" --file "$compose_file" ps --quiet backend)"
image_id="$(docker inspect --format '{{.Image}}' "$backend_id")"
docker compose --project-name "$project" --file "$compose_file" exec -T \
  -e PORTFOLIO_EXPECTED_REVISION="${VCS_REF:?VCS_REF is required}" \
  -e PORTFOLIO_IMAGE_ID="$image_id" \
  -e PORTFOLIO_ISOLATED_SMOKE=synthetic-disposable-only \
  backend node dist/scripts/smoke-portfolio-runtime.js

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

if [[ "$recovery_rehearsal" != "github-hosted-v1" ]]; then
  echo "Isolated API and UI smoke checks passed for Compose project $project"
  exit 0
fi

services=(db backend frontend nginx)
declare -A runtime_ids=() runtime_images=() runtime_records=() volume_records=()

capture_runtime() {
  local service id record actual_id actual_image project_label service_label restart mount_record volume name
  local volume_driver volume_created_at volume_project volume_logical
  local -a ids=()
  runtime_ids=() runtime_images=() runtime_records=() volume_records=()
  for service in "${services[@]}"; do
    id="$(docker compose --project-name "$project" --file "$compose_file" ps --quiet "$service")"
    [[ "$id" =~ ^[0-9a-f]{12,64}$ ]] || { echo "Missing fixed CI service container: $service" >&2; return 1; }
    record="$(docker inspect --format '{{.Id}}|{{.Image}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{.HostConfig.RestartPolicy.Name}}|{{json .Mounts}}' "$id")"
    IFS='|' read -r actual_id actual_image project_label service_label restart mount_record <<<"$record"
    [[ "$actual_id" =~ ^[0-9a-f]{64}$ && "$actual_image" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "Incomplete immutable service identity: $service" >&2; return 1; }
    [[ "$project_label" == "$project" && "$service_label" == "$service" && "$restart" == "unless-stopped" ]] || {
      echo "Service escaped the fixed synthetic Compose identity: $service" >&2
      return 1
    }
    runtime_ids[$service]="$actual_id"
    runtime_images[$service]="$actual_image"
    runtime_records[$service]="$record"
    ids+=("$actual_id")
  done

  local running_ids owned_ids
  running_ids="$(docker ps -q --no-trunc | sort)"
  owned_ids="$(printf '%s\n' "${ids[@]}" | sort)"
  [[ "$running_ids" == "$owned_ids" ]] || { echo "Unexpected running container set on the GitHub-hosted VM" >&2; return 1; }
  [[ -z "$(docker ps -aq --filter label=com.docker.compose.project=belib-production)" ]] || {
    echo "Production project container unexpectedly exists on the GitHub-hosted VM" >&2
    return 1
  }
  for volume in postgres-data book-files; do
    name="${project}_${volume}"
    record="$(docker volume inspect --format '{{.Name}}|{{.Driver}}|{{.CreatedAt}}|{{index .Labels "com.docker.compose.project"}}|{{index .Labels "com.docker.compose.volume"}}' "$name")"
    IFS='|' read -r actual_id volume_driver volume_created_at volume_project volume_logical <<<"$record"
    [[ "$actual_id" == "$name" && "$volume_driver" == local && -n "$volume_created_at" \
      && "$volume_project" == "$project" && "$volume_logical" == "$volume" ]] || {
      echo "Named volume identity mismatch" >&2
      return 1
    }
    volume_records[$volume]="$record"
  done
}

wait_service_running() {
  local service="$1" expected_id="$2" expected_image="$3" attempt current status image
  for attempt in {1..60}; do
    if record="$(docker inspect --format '{{.State.Running}}|{{.Id}}|{{.Image}}' "$expected_id" 2>/dev/null)"; then
      IFS='|' read -r status current image <<<"$record"
      [[ "$status" == true && "$current" == "$expected_id" && "$image" == "$expected_image" ]] && return 0
    fi
    sleep 1
  done
  echo "Container did not recover with the same ID/image: $service" >&2
  return 1
}

wait_for_http_recovery() {
  local attempt health ui health_body
  for attempt in {1..60}; do
    health_body="$(mktemp)"
    health="$(curl --silent --show-error --connect-timeout 3 --max-time 5 --output "$health_body" --write-out '%{http_code}' "$api_url/health" || true)"
    ui="$(curl --silent --show-error --connect-timeout 3 --max-time 5 --output /dev/null --write-out '%{http_code}' "$ui_url/" || true)"
    if [[ "$health" == 200 && "$ui" =~ ^2[0-9][0-9]$ ]] \
      && grep -Eiq '"status"[[:space:]]*:[[:space:]]*"ok"' "$health_body" \
      && grep -Eiq '"service"[[:space:]]*:[[:space:]]*"belib-api"' "$health_body"; then
      rm -f "$health_body"
      return 0
    fi
    rm -f "$health_body"
    sleep 2
  done
  echo "Synthetic API/UI did not recover on loopback" >&2
  return 1
}

wait_db_healthy() {
  local id="$1" attempt status
  for attempt in {1..60}; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$id" 2>/dev/null || true)"
    [[ "$status" == healthy ]] && return 0
    sleep 1
  done
  echo "Synthetic PostgreSQL did not become healthy" >&2
  return 1
}

capture_runtime
baseline_db_id="${runtime_ids[db]}"
baseline_frontend_id="${runtime_ids[frontend]}"
baseline_db_image="${runtime_images[db]}"
baseline_frontend_image="${runtime_images[frontend]}"
baseline_db="${runtime_records[db]}"
baseline_backend="${runtime_records[backend]}"
baseline_frontend="${runtime_records[frontend]}"
baseline_nginx="${runtime_records[nginx]}"
baseline_postgres_volume="${volume_records[postgres-data]}"
baseline_books_volume="${volume_records[book-files]}"
marker="ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
docker compose --project-name "$project" --file "$compose_file" exec -T db \
  psql -qAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "CREATE TABLE ci_recovery_probe (marker text PRIMARY KEY); INSERT INTO ci_recovery_probe VALUES ('$marker');" >/dev/null

docker kill "$baseline_frontend_id" >/dev/null
wait_service_running frontend "$baseline_frontend_id" "$baseline_frontend_image"
wait_for_http_recovery
capture_runtime
docker kill "$baseline_db_id" >/dev/null
wait_service_running db "$baseline_db_id" "$baseline_db_image"
wait_db_healthy "$baseline_db_id"
db_continuity="$(docker compose --project-name "$project" --file "$compose_file" exec -T db \
  psql -qAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT count(*) = 1 AND min(marker) = '$marker' FROM ci_recovery_probe;")"
[[ "$db_continuity" == t ]] || { echo "Synthetic DB marker lost after ordinary crash" >&2; exit 1; }
wait_for_http_recovery
capture_runtime
[[ "${runtime_records[db]}" == "$baseline_db" && "${runtime_records[backend]}" == "$baseline_backend" \
  && "${runtime_records[frontend]}" == "$baseline_frontend" && "${runtime_records[nginx]}" == "$baseline_nginx" \
  && "${volume_records[postgres-data]}" == "$baseline_postgres_volume" \
  && "${volume_records[book-files]}" == "$baseline_books_volume" ]] || {
  echo "Identity changed during ordinary crash recovery" >&2
  exit 1
}

capture_runtime
echo "Restarting Docker only on the guarded GitHub-hosted VM"
sudo systemctl restart docker
for attempt in {1..60}; do
  docker info >/dev/null 2>&1 && break
  [[ "$attempt" != 60 ]] || { echo "Disposable Docker daemon did not return" >&2; exit 1; }
  sleep 2
done
for service in "${services[@]}"; do
  wait_service_running "$service" "${runtime_ids[$service]}" "${runtime_images[$service]}"
done
wait_db_healthy "${runtime_ids[db]}"
wait_for_http_recovery
db_continuity="$(docker compose --project-name "$project" --file "$compose_file" exec -T db \
  psql -qAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT count(*) = 1 AND min(marker) = '$marker' FROM ci_recovery_probe;")"
[[ "$db_continuity" == t ]] || { echo "Synthetic DB marker lost after Docker daemon restart" >&2; exit 1; }
post_restart_network_identity="$(docker network inspect --format '{{.Name}}|{{.Internal}}|{{index .Labels "com.docker.compose.project"}}|{{index .Labels "com.docker.compose.network"}}' "$network")"
[[ "$post_restart_network_identity" == "$network_identity" ]] || {
  echo "Synthetic Compose network identity changed after daemon restart" >&2
  exit 1
}
capture_runtime
[[ "${runtime_records[db]}" == "$baseline_db" && "${runtime_records[backend]}" == "$baseline_backend" \
  && "${runtime_records[frontend]}" == "$baseline_frontend" && "${runtime_records[nginx]}" == "$baseline_nginx" \
  && "${volume_records[postgres-data]}" == "$baseline_postgres_volume" \
  && "${volume_records[book-files]}" == "$baseline_books_volume" ]] || {
  echo "Container, image, or named volume identity changed after daemon restart" >&2
  exit 1
}

echo "Isolated API/UI, ordinary crash, daemon restart, container/image/volume identity, and synthetic DB continuity passed for $project"
