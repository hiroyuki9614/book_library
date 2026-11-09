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
  if ! docker_endpoint="$(command docker --host unix:///var/run/docker.sock context inspect \
    --format '{{.Endpoints.docker.Host}}' 2>/dev/null)" \
    || [[ "$docker_endpoint" != "unix:///var/run/docker.sock" ]]; then
    echo "Refusing recovery rehearsal: Docker context is not the runner-local socket" >&2
    exit 1
  fi
  docker() {
    command docker --host unix:///var/run/docker.sock "$@"
  }
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

restore_backup_dir=""
cleanup() {
  if [[ -n "${restore_backup_dir:-}" ]]; then
    rm -rf -- "$restore_backup_dir" || true
  fi
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

wait_runtime_healthcheck() {
  local service="$1" expected_id="$2" expected_image="$3" attempt record health_test
  local current_id current_image status
  [[ "$recovery_rehearsal" == "github-hosted-v1" && "$RUNNER_ENVIRONMENT" == "github-hosted" \
    && "$GITHUB_ACTIONS" == true ]] || {
    echo "Refusing runtime healthcheck outside guarded GitHub-hosted rehearsal" >&2
    return 1
  }
  case "$service" in
    frontend|nginx) ;;
    *) echo "Refusing runtime healthcheck outside fixed frontend/nginx services" >&2; return 1 ;;
  esac
  for attempt in {1..60}; do
    if ! record="$(docker inspect --format '{{json .Config.Healthcheck.Test}}|{{.Id}}|{{.Image}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$expected_id" 2>/dev/null)"; then
      sleep 2
      continue
    fi
    IFS='|' read -r health_test current_id current_image status <<<"$record"
    if [[ "$current_id" != "$expected_id" || "$current_image" != "$expected_image" ]]; then
      echo "Synthetic $service container identity changed during healthcheck verification" >&2
      return 1
    fi
    if ! python3 -c '
import json
import sys

expected = ["CMD", "busybox", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"]
try:
    actual = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    raise SystemExit(1)
raise SystemExit(0 if actual == expected else 1)
' "$health_test"; then
      echo "Synthetic $service container does not have the expected loopback healthcheck" >&2
      return 1
    fi
    if [[ "$status" == missing ]]; then
      echo "Synthetic $service container has no Docker health status" >&2
      return 1
    fi
    if [[ "$status" == healthy ]]; then
      echo "Synthetic $service runtime healthcheck became healthy"
      return 0
    fi
    sleep 2
  done
  echo "Synthetic $service runtime healthcheck did not become healthy within 120 seconds" >&2
  return 1
}

synthetic_restore_drill() {
  local marker="$1" restore_db backup_file restore_error_file restore_error_category restored_marker
  local -a restore_pipeline_status=()

  [[ "$recovery_rehearsal" == "github-hosted-v1" ]] || {
    echo "Refusing synthetic restore outside guarded GitHub-hosted rehearsal" >&2
    return 1
  }
  restore_db="ci_restore_${GITHUB_RUN_ID}_${GITHUB_RUN_ATTEMPT}"
  [[ "$restore_db" =~ ^[a-z][a-z0-9_]{0,62}$ ]] || {
    echo "Synthetic restore database identity is invalid" >&2
    return 1
  }

  restore_backup_dir="$(mktemp -d "/tmp/belib-recovery-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.XXXXXX")" || {
    echo "Could not create private synthetic backup directory" >&2
    return 1
  }
  chmod 700 "$restore_backup_dir" || {
    echo "Could not secure private synthetic backup directory" >&2
    return 1
  }
  backup_file="$(mktemp "$restore_backup_dir/postgres.XXXXXX")" || {
    echo "Could not create private synthetic backup file" >&2
    return 1
  }
  chmod 600 "$backup_file" || {
    echo "Could not secure private synthetic backup file" >&2
    return 1
  }
  restore_error_file="$(mktemp "$restore_backup_dir/pg-restore-error.XXXXXX")" || {
    echo "Could not create private synthetic restore diagnostic file" >&2
    return 1
  }
  chmod 600 "$restore_error_file" || {
    echo "Could not secure private synthetic restore diagnostic file" >&2
    return 1
  }

  if ! docker compose --project-name "$project" --file "$compose_file" exec -T db \
    pg_dump --format=custom -U "$POSTGRES_USER" -d "$POSTGRES_DB" >"$backup_file" 2>/dev/null; then
    echo "Synthetic PostgreSQL custom-format dump failed" >&2
    return 1
  fi
  if [[ ! -s "$backup_file" ]]; then
    echo "Synthetic PostgreSQL custom-format dump is empty" >&2
    return 1
  fi
  # Keep only a bounded private sample for classification. The helper consumes
  # all stderr so pg_restore is not affected by a closed pipe, but writes at
  # most 16 KiB and never prints the captured text.
  # Validate the dump as a custom archive before creating the restore database.
  # This distinguishes a bad source/archive or exec from SQL restore failures.
  if docker compose --project-name "$project" --file "$compose_file" exec -T db \
    pg_restore --list <"$backup_file" 2>&1 >/dev/null \
    | python3 -c '
import sys
try:
    data = sys.stdin.buffer.read(16384)
    with open(sys.argv[1], "wb") as capture:
        capture.write(data)
    sys.stdin.buffer.read()
except OSError:
    raise SystemExit(1)
' "$restore_error_file"; then
    :
  else
    restore_pipeline_status=("${PIPESTATUS[@]}")
    restore_error_category="archive_validation_failure"
    if grep -Eiq 'Error response from daemon|OCI runtime exec failed|executable file not found|container[^[:cntrl:]]{0,80}is not running|service[^[:cntrl:]]{0,80}is not running' "$restore_error_file"; then
      restore_error_category="compose_exec_failure"
    elif [[ "${restore_pipeline_status[1]:-unknown}" != 0 ]]; then
      restore_error_category="diagnostic_capture_failure"
    elif grep -Eiq 'unsupported version|version[^[:cntrl:]]{0,80}not supported|archive version' "$restore_error_file"; then
      restore_error_category="unsupported_archive_version"
    elif grep -Eiq 'did not find magic string|appears to be a text format dump|unexpected end of file|unexpected eof|could not read from input file' "$restore_error_file"; then
      restore_error_category="invalid_archive"
    fi
    echo "Synthetic PostgreSQL archive preflight failed (diagnostic category: $restore_error_category; compose pg_restore exit: ${restore_pipeline_status[0]:-unknown}; diagnostic capture exit: ${restore_pipeline_status[1]:-unknown})" >&2
    return 1
  fi
  if ! docker compose --project-name "$project" --file "$compose_file" exec -T db \
    createdb --template=template0 -U "$POSTGRES_USER" "$restore_db" >/dev/null 2>&1; then
    echo "Could not create separate synthetic restore database" >&2
    return 1
  fi
  # Compose exec keeps stdin interactive by default; -T disables only the
  # pseudo-TTY while the custom archive is streamed to pg_restore.
  if docker compose --project-name "$project" --file "$compose_file" exec -T db \
    pg_restore --exit-on-error --no-owner --no-privileges -U "$POSTGRES_USER" \
    -d "$restore_db" <"$backup_file" 2>&1 >/dev/null \
    | python3 -c '
import sys
try:
    data = sys.stdin.buffer.read(16384)
    with open(sys.argv[1], "wb") as capture:
        capture.write(data)
    sys.stdin.buffer.read()
except OSError:
    raise SystemExit(1)
' "$restore_error_file"; then
    :
  else
    restore_pipeline_status=("${PIPESTATUS[@]}")
    restore_error_category="restore_sql_failure"
    if grep -Eiq 'Error response from daemon|OCI runtime exec failed|executable file not found|container[^[:cntrl:]]{0,80}is not running|service[^[:cntrl:]]{0,80}is not running' "$restore_error_file"; then
      restore_error_category="compose_exec_failure"
    elif [[ "${restore_pipeline_status[1]:-unknown}" != 0 ]]; then
      restore_error_category="diagnostic_capture_failure"
    elif grep -Eiq 'unsupported version|version[^[:cntrl:]]{0,80}not supported|archive version' "$restore_error_file"; then
      restore_error_category="unsupported_archive_version"
    elif grep -Eiq 'did not find magic string|appears to be a text format dump|unexpected end of file|unexpected eof|could not read from input file' "$restore_error_file"; then
      restore_error_category="invalid_archive"
    elif grep -Eiq 'does not exist|undefined (function|type)|no such (function|type)' "$restore_error_file"; then
      restore_error_category="missing_database_object"
    elif grep -Eiq 'permission denied|must be owner|not authorized|insufficient privilege' "$restore_error_file"; then
      restore_error_category="permission_failure"
    elif grep -Eiq 'already exists|duplicate key|duplicate_object' "$restore_error_file"; then
      restore_error_category="object_conflict"
    elif grep -Eiq 'could not connect|connection refused|server closed the connection|connection to server' "$restore_error_file"; then
      restore_error_category="connection_failure"
    fi
    echo "Synthetic PostgreSQL restore failed (diagnostic category: $restore_error_category; compose pg_restore exit: ${restore_pipeline_status[0]:-unknown}; diagnostic capture exit: ${restore_pipeline_status[1]:-unknown})" >&2
    return 1
  fi
  if ! restored_marker="$(docker compose --project-name "$project" --file "$compose_file" exec -T db \
    psql -qAt -U "$POSTGRES_USER" -d "$restore_db" \
    -c "SELECT EXISTS (SELECT 1 FROM ci_recovery_probe WHERE marker = '$marker');" 2>/dev/null)"; then
    echo "Could not verify marker in synthetic restored database" >&2
    return 1
  fi
  [[ "$restored_marker" == t ]] || {
    echo "Expected synthetic marker is missing from restored database" >&2
    return 1
  }
  echo "Synthetic PostgreSQL custom-format restore marker verified"
}

# Docker API kill is a manual stop: unless-stopped must not auto-restart it.
# Exercise an unexpected init-process crash only on the preflight-verified,
# disposable GitHub-hosted VM. pidfd pins the process despite PID reuse.
crash_owned_service() {
  local service="$1" expected_id="$2" expected_image="$3"
  local record actual_id actual_image host_pid running restart_count started_at project_label service_label
  local current_id current_image current_running current_restart current_started attempt

  [[ "$recovery_rehearsal" == "github-hosted-v1" && "$RUNNER_ENVIRONMENT" == "github-hosted" \
    && "$GITHUB_ACTIONS" == true ]] || {
    echo "Refusing synthetic crash outside guarded GitHub-hosted rehearsal" >&2
    return 1
  }
  [[ "$(docker context inspect --format '{{.Endpoints.docker.Host}}')" == "unix:///var/run/docker.sock" ]] || {
    echo "Refusing synthetic crash outside runner-local Docker" >&2
    return 1
  }
  capture_runtime
  [[ "${runtime_ids[$service]}" == "$expected_id" && "${runtime_images[$service]}" == "$expected_image" ]] || {
    echo "Refusing synthetic crash: expected container/image identity changed" >&2
    return 1
  }
  record="$(docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Pid}}|{{.State.Running}}|{{.RestartCount}}|{{.State.StartedAt}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}' "$expected_id")"
  IFS='|' read -r actual_id actual_image host_pid running restart_count started_at project_label service_label <<<"$record"
  [[ "$actual_id" == "$expected_id" && "$actual_image" == "$expected_image" \
    && "$running" == true && "$project_label" == "$project" && "$service_label" == "$service" \
    && "$host_pid" =~ ^[0-9]+$ && "$host_pid" -gt 1 \
    && "$restart_count" =~ ^[0-9]+$ && -n "$started_at" ]] || {
    echo "Refusing synthetic crash: running PID/identity receipt is invalid" >&2
    return 1
  }

  # The cgroup must identify the exact synthetic container. Once opened,
  # pidfd_send_signal cannot signal a recycled host PID.
  # Pin the init PID; check Docker's exact live receipt again *after* opening
  # the pidfd, so a recycled PID or unrelated auto-restart cannot pass.
  sudo python3 - "$host_pid" "$expected_id" "$expected_image" "$restart_count" "$started_at" <<'PYCRASH'
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

pid, expected_id, expected_image, restart_count, started_at = (
    int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
)
if (
    os.uname().sysname != "Linux"
    or not hasattr(os, "pidfd_open")
    or not hasattr(signal, "pidfd_send_signal")
    or len(expected_id) != 64
    or not all(c in "0123456789abcdef" for c in expected_id)
):
    raise SystemExit("Refusing synthetic crash: platform or container identity unsupported")

def proc_start_ticks():
    record = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return record.rsplit(")", 1)[1].strip().split()[19]

def docker_cgroup_matches(cgroup, container_id):
    import re

    def exact_component(value):
        return value == container_id or value == f"docker-{container_id}.scope"

    def docker_identity(value):
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
        match = re.fullmatch(r"docker-([0-9a-f]{64})\.scope", value)
        return match.group(1) if match else None

    lines = [line.split(":", 2) for line in cgroup.splitlines()]
    if not lines or any(len(line) != 3 for line in lines):
        return False
    v2_lines = [line for line in lines if line[0] == "0" and line[1] == ""]
    v1_lines = [line for line in lines if line[0].isdigit() and int(line[0]) > 0 and line[1]]
    classified_lines = v2_lines + v1_lines
    if (
        len(classified_lines) != len(lines)
        or len(v2_lines) > 1
        or (v2_lines and v1_lines)
        or not (v2_lines or v1_lines)
    ):
        return False
    relevant_lines = v2_lines or v1_lines
    hierarchy_ids = set()
    controllers = set()
    identities = set()
    for hierarchy, controller_list, path in relevant_lines:
        if not path.startswith("/") or any(part in (".", "..") for part in path.split("/")):
            return False
        if hierarchy == "0":
            if controller_list:
                return False
        else:
            controller_names = controller_list.split(",")
            if (
                hierarchy in hierarchy_ids
                or any(not re.fullmatch(r"[A-Za-z0-9_.=-]+", name) for name in controller_names)
                or len(controller_names) != len(set(controller_names))
                or controllers.intersection(controller_names)
            ):
                return False
            hierarchy_ids.add(hierarchy)
            controllers.update(controller_names)
        line_identities = {
            identity
            for component in path.split("/")
            if (identity := docker_identity(component)) is not None
        }
        if len(line_identities) != 1:
            return False
        identities.update(line_identities)
    return identities == {container_id}

def intended_pidfd_exit_observed(events, pidfd, pollin):
    return any(event_fd == pidfd and event_mask & pollin for event_fd, event_mask in events)

try:
    ticks_before = proc_start_ticks()
    fd = os.pidfd_open(pid, 0)
except (OSError, AttributeError, IndexError, ValueError):
    raise SystemExit("Refusing synthetic crash: cannot pin and fingerprint container init")
try:
    try:
        cgroup = Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8")
        proc_status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        ticks_after = proc_start_ticks()
    except (OSError, IndexError, ValueError):
        raise SystemExit("Refusing synthetic crash: process identity unreadable")
    ns_lines = [line.split()[1:] for line in proc_status.splitlines() if line.startswith("NSpid:")]
    if (
        ticks_before != ticks_after
        or not docker_cgroup_matches(cgroup, expected_id)
        or len(ns_lines) != 1
        or len(ns_lines[0]) < 2
        or ns_lines[0][0] != str(pid)
        or ns_lines[0][-1] != "1"
    ):
        raise SystemExit("Refusing synthetic crash: cgroup/PID1/start-time identity changed")

    # Fixed local Unix socket: no production/remote Docker context is consulted.
    check = subprocess.run(
        [
            "docker", "--host", "unix:///var/run/docker.sock", "inspect", "--format",
            "{{.Id}}|{{.Image}}|{{.State.Pid}}|{{.State.Running}}|{{.State.StartedAt}}|{{.RestartCount}}",
            expected_id,
        ],
        check=False, capture_output=True, text=True, timeout=5,
    )
    observed = check.stdout.strip().split("|")
    if check.returncode != 0 or observed != [
        expected_id, expected_image, str(pid), "true", started_at, restart_count
    ]:
        raise SystemExit("Refusing synthetic crash: Docker live init/restart receipt changed")

    try:
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        events = poller.poll(10000)
        if not intended_pidfd_exit_observed(events, fd, select.POLLIN):
            raise SystemExit("Refusing synthetic crash: intended init exit not observed")
    except OSError:
        raise SystemExit("Refusing synthetic crash: pinned init crash could not be proven")
finally:
    os.close(fd)
PYCRASH

  # Require a new start and increased Docker restart counter, not a stale
  # running snapshot or an explicit docker start/up masking failed recovery.
  for attempt in {1..60}; do
    if record="$(docker inspect --format '{{.Id}}|{{.Image}}|{{.State.Running}}|{{.RestartCount}}|{{.State.StartedAt}}' "$expected_id" 2>/dev/null)"; then
      IFS='|' read -r current_id current_image current_running current_restart current_started <<<"$record"
      if [[ "$current_id" == "$expected_id" && "$current_image" == "$expected_image" \
        && "$current_running" == true && "$current_restart" =~ ^[0-9]+$ \
        && "$current_restart" -gt "$restart_count" && "$current_started" != "$started_at" ]]; then
        return 0
      fi
    fi
    sleep 1
  done
  echo "Unexpected crash was not automatically recovered with the same container ID/image: $service" >&2
  return 1
}

safe_runtime_receipt() {
  local record="$1" id image project_label service_label restart mount_record mounts_hash
  IFS='|' read -r id image project_label service_label restart mount_record <<<"$record"
  mounts_hash="$(printf '%s' "$mount_record" | sha256sum | cut -d' ' -f1)"
  printf 'id=%s image=%s project=%s service=%s restart=%s mounts_sha256=%s' \
    "$id" "$image" "$project_label" "$service_label" "$restart" "$mounts_hash"
}
safe_mount_diff() {
  local before_record="$1" after_record="$2"
  printf '%s\0%s\0' "$before_record" "$after_record" | python3 -c '
import json
import sys
from collections import Counter

def reject_non_json_constant(value):
    raise ValueError("invalid JSON constant")

def reject_json_number(value):
    raise ValueError("numeric JSON value is not valid in a Docker mount receipt")

def reject_duplicate_object_properties(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object property")
        result[key] = value
    return result

try:
    records = sys.stdin.buffer.read().split(b"\0")
    if len(records) != 3 or records[-1] != b"":
        raise ValueError("invalid receipt frame")
    before, after = (
        json.loads(
            record.split(b"|", 5)[5],
            parse_constant=reject_non_json_constant,
            parse_int=reject_json_number,
            parse_float=reject_json_number,
            object_pairs_hook=reject_duplicate_object_properties,
        )
        for record in records[:2]
    )
    if not isinstance(before, list) or not isinstance(after, list):
        raise ValueError("mount receipt is not an array")
    if not all(isinstance(mount, dict) for mount in before + after):
        raise ValueError("mount receipt contains a non-object entry")
    canonical = lambda mounts: Counter(
        json.dumps(mount, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        for mount in mounts
    )
except (IndexError, json.JSONDecodeError, TypeError, ValueError):
    print("mount field differences unavailable (invalid Docker mount receipt)")
    raise SystemExit(1)

if canonical(before) == canonical(after):
    raise SystemExit(0)

differences = []
docker_mount_fields = {
    "Type", "Name", "Source", "Destination", "Driver", "Mode", "RW", "Propagation"
}

def compare(path, left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(left.keys() | right.keys(), key=str):
            # Only known Docker mount field names are safe to expose in CI output.
            safe_key = key if key in docker_mount_fields else "<other-field>"
            child = f"{path}.{safe_key}"
            if key not in left or key not in right:
                differences.append(child)
            else:
                compare(child, left[key], right[key])
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            differences.append(f"{path}.count")
        for index, (old, new) in enumerate(zip(left, right)):
            compare(f"{path}[{index}]", old, new)
    elif left != right:
        differences.append(path)

compare("Mounts", before, after)
if differences:
    print("mount field differences: " + ", ".join(differences))
else:
    print("mount field differences: entries changed")
raise SystemExit(1)
'
}
same_runtime_identity() {
  local before_record="$1" after_record="$2"
  local before_id before_image before_project before_service before_restart before_mounts
  local after_id after_image after_project after_service after_restart after_mounts
  IFS='|' read -r before_id before_image before_project before_service before_restart before_mounts <<<"$before_record"
  IFS='|' read -r after_id after_image after_project after_service after_restart after_mounts <<<"$after_record"
  [[ "$before_id|$before_image|$before_project|$before_service|$before_restart" == \
    "$after_id|$after_image|$after_project|$after_service|$after_restart" ]]
}
sha256_value() {
  printf '%s' "$1" | sha256sum | cut -d' ' -f1
}
verify_ordinary_crash_identity() {
  local service before_record after_record before_id before_image before_project before_service before_restart before_mounts
  local after_id after_image after_project after_service after_restart after_mounts mount_diff field before_value after_value
  local volume before_volume after_volume before_name before_driver before_created before_project_label before_logical
  local after_name after_driver after_created after_project_label after_logical
  local changed=false
  for service in "${services[@]}"; do
    case "$service" in
      db) before_record="$baseline_db" ;;
      backend) before_record="$baseline_backend" ;;
      frontend) before_record="$baseline_frontend" ;;
      nginx) before_record="$baseline_nginx" ;;
    esac
    after_record="${runtime_records[$service]}"
    IFS='|' read -r before_id before_image before_project before_service before_restart before_mounts <<<"$before_record"
    IFS='|' read -r after_id after_image after_project after_service after_restart after_mounts <<<"$after_record"
    if ! same_runtime_identity "$before_record" "$after_record"; then
      changed=true
    fi
    for field in Id Image ComposeProject ComposeService RestartPolicy; do
      case "$field" in
        Id) before_value="$before_id"; after_value="$after_id" ;;
        Image) before_value="$before_image"; after_value="$after_image" ;;
        ComposeProject) before_value="$before_project"; after_value="$after_project" ;;
        ComposeService) before_value="$before_service"; after_value="$after_service" ;;
        RestartPolicy) before_value="$before_restart"; after_value="$after_restart" ;;
      esac
      if [[ "$before_value" != "$after_value" ]]; then
        changed=true
        printf 'Ordinary-crash identity mismatch: service=%s field=%s before_sha256=%s after_sha256=%s\n' \
          "$service" "$field" "$(sha256_value "$before_value")" "$(sha256_value "$after_value")" >&2
      fi
    done
    if ! mount_diff="$(safe_mount_diff "$before_record" "$after_record")"; then
      changed=true
      printf 'Ordinary-crash identity mismatch: service=%s field=Mounts before_sha256=%s after_sha256=%s\n' \
        "$service" "$(sha256_value "$before_mounts")" "$(sha256_value "$after_mounts")" >&2
      [[ -z "$mount_diff" ]] || printf '%s\n' "$mount_diff" >&2
    fi
  done
  for volume in postgres-data book-files; do
    case "$volume" in
      postgres-data) before_volume="$baseline_postgres_volume" ;;
      book-files) before_volume="$baseline_books_volume" ;;
    esac
    after_volume="${volume_records[$volume]}"
    IFS='|' read -r before_name before_driver before_created before_project_label before_logical <<<"$before_volume"
    IFS='|' read -r after_name after_driver after_created after_project_label after_logical <<<"$after_volume"
    for field in Name Driver CreatedAt ComposeProject ComposeVolume; do
      case "$field" in
        Name) before_value="$before_name"; after_value="$after_name" ;;
        Driver) before_value="$before_driver"; after_value="$after_driver" ;;
        CreatedAt) before_value="$before_created"; after_value="$after_created" ;;
        ComposeProject) before_value="$before_project_label"; after_value="$after_project_label" ;;
        ComposeVolume) before_value="$before_logical"; after_value="$after_logical" ;;
      esac
      if [[ "$before_value" != "$after_value" ]]; then
        changed=true
        printf 'Ordinary-crash identity mismatch: service=%s field=%s before_sha256=%s after_sha256=%s\n' \
          "$volume" "$field" "$(sha256_value "$before_value")" "$(sha256_value "$after_value")" >&2
      fi
    done
  done
  [[ "$changed" == false ]]
}
safe_volume_receipt() {
  local record="$1" name driver created_at project_label logical_name
  IFS='|' read -r name driver created_at project_label logical_name <<<"$record"
  printf 'name=%s driver=%s created_at=%s project=%s logical_name=%s' \
    "$name" "$driver" "$created_at" "$project_label" "$logical_name"
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
wait_runtime_healthcheck frontend "$baseline_frontend_id" "$baseline_frontend_image"
wait_runtime_healthcheck nginx "${runtime_ids[nginx]}" "${runtime_images[nginx]}"
marker="ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
docker compose --project-name "$project" --file "$compose_file" exec -T db \
  psql -qAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "CREATE TABLE ci_recovery_probe (marker text PRIMARY KEY); INSERT INTO ci_recovery_probe VALUES ('$marker');" >/dev/null
synthetic_restore_drill "$marker"

crash_owned_service frontend "$baseline_frontend_id" "$baseline_frontend_image"
wait_service_running frontend "$baseline_frontend_id" "$baseline_frontend_image"
wait_for_http_recovery
capture_runtime
crash_owned_service db "$baseline_db_id" "$baseline_db_image"
wait_service_running db "$baseline_db_id" "$baseline_db_image"
wait_db_healthy "$baseline_db_id"
db_continuity="$(docker compose --project-name "$project" --file "$compose_file" exec -T db \
  psql -qAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT count(*) = 1 AND min(marker) = '$marker' FROM ci_recovery_probe;")"
[[ "$db_continuity" == t ]] || { echo "Synthetic DB marker lost after ordinary crash" >&2; exit 1; }
wait_for_http_recovery
capture_runtime
if ! verify_ordinary_crash_identity; then
  echo "Identity changed during ordinary crash recovery" >&2
  exit 1
fi

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
wait_runtime_healthcheck frontend "${runtime_ids[frontend]}" "${runtime_images[frontend]}"
wait_runtime_healthcheck nginx "${runtime_ids[nginx]}" "${runtime_images[nginx]}"
wait_db_healthy "${runtime_ids[db]}"
wait_for_http_recovery
db_continuity="$(docker compose --project-name "$project" --file "$compose_file" exec -T db \
  psql -qAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT count(*) = 1 AND min(marker) = '$marker' FROM ci_recovery_probe;")"
[[ "$db_continuity" == t ]] || { echo "Synthetic DB marker lost after Docker daemon restart" >&2; exit 1; }
verify_post_restart_network_identity() {
  local expected="$1" observed
  if ! observed="$(docker network inspect --format '{{.Name}}|{{.Internal}}|{{index .Labels "com.docker.compose.project"}}|{{index .Labels "com.docker.compose.network"}}' "$network" 2>/dev/null)"; then
    echo "Synthetic Compose network identity changed after daemon restart" >&2
    echo "Expected network receipt: $expected" >&2
    echo "Observed network receipt: unavailable (Docker network inspect failed)" >&2
    return 1
  fi
  if [[ "$observed" != "$expected" ]]; then
    echo "Synthetic Compose network identity changed after daemon restart" >&2
    echo "Expected network receipt: $expected" >&2
    echo "Observed network receipt: $observed" >&2
    return 1
  fi
}
verify_post_restart_network_identity "$network_identity"
capture_runtime
runtime_identity_changed=false
for service in "${services[@]}"; do
  case "$service" in
    db) before_record="$baseline_db" ;;
    backend) before_record="$baseline_backend" ;;
    frontend) before_record="$baseline_frontend" ;;
    nginx) before_record="$baseline_nginx" ;;
  esac
  mount_diff=""
  if ! same_runtime_identity "$before_record" "${runtime_records[$service]}" \
    || ! mount_diff="$(safe_mount_diff "$before_record" "${runtime_records[$service]}")"; then
    runtime_identity_changed=true
    echo "Post-daemon-restart container receipt changed: $service" >&2
    echo "Before: $(safe_runtime_receipt "$before_record")" >&2
    echo "After:  $(safe_runtime_receipt "${runtime_records[$service]}")" >&2
    [[ -z "$mount_diff" ]] || echo "$mount_diff" >&2
  fi
done
for volume in postgres-data book-files; do
  case "$volume" in
    postgres-data) before_record="$baseline_postgres_volume" ;;
    book-files) before_record="$baseline_books_volume" ;;
  esac
  if [[ "${volume_records[$volume]}" != "$before_record" ]]; then
    echo "Post-daemon-restart volume receipt changed: $volume" >&2
    echo "Before: $(safe_volume_receipt "$before_record")" >&2
    echo "After:  $(safe_volume_receipt "${volume_records[$volume]}")" >&2
  fi
done
[[ "$runtime_identity_changed" == false \
  && "${volume_records[postgres-data]}" == "$baseline_postgres_volume" \
  && "${volume_records[book-files]}" == "$baseline_books_volume" ]] || {
  echo "Container, image, or named volume identity changed after daemon restart" >&2
  exit 1
}

echo "Isolated API/UI, ordinary crash, daemon restart, container/image/volume identity, and synthetic DB continuity passed for $project"
