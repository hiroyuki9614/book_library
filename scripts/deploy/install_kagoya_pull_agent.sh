#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: scripts/deploy/install_kagoya_pull_agent.sh /existing/production/.env" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
PRODUCTION_ENV_FILE=$(realpath -e -- "$1")
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPOSITORY_ROOT=$(git -C "$SCRIPT_DIR/../.." rev-parse --show-toplevel)
CONTAINER_NAME=belib-production-frontend-1
UNIT_NAME=belib-production-cd

[[ "$(id -un)" == agent ]] || { echo "error: run this bootstrap as the existing KAGOYA agent user" >&2; exit 1; }
for command in docker git python3 systemctl loginctl realpath install getent env; do
  command -v "$command" >/dev/null || { echo "error: required command is unavailable: $command" >&2; exit 1; }
done

[[ -f "$PRODUCTION_ENV_FILE" ]] || { echo "error: the existing production Compose env file is unavailable" >&2; exit 1; }
python3 - "$PRODUCTION_ENV_FILE" <<'PY'
import os, stat, sys
mode = stat.S_IMODE(os.stat(sys.argv[1]).st_mode)
if mode & 0o077:
    raise SystemExit("error: production env file must not be group/world accessible")
PY

DOCKER_SECURITY=$(docker info --format '{{json .SecurityOptions}}' 2>/dev/null) || {
  echo "error: rootless Docker is unavailable to agent" >&2
  exit 1
}
[[ "$DOCKER_SECURITY" == *rootless* ]] || { echo "error: Docker is not running rootless" >&2; exit 1; }

LINGER=$(loginctl show-user agent -p Linger --value 2>/dev/null || true)
[[ "$LINGER" == yes ]] || { echo "error: the agent user systemd manager is not persistent; no service was installed" >&2; exit 1; }
systemctl --user show-environment >/dev/null 2>&1 || { echo "error: the agent user systemd manager is unavailable" >&2; exit 1; }

CONFIG_DIR=$(getent passwd agent | cut -d: -f6)
[[ -n "$CONFIG_DIR" && -d "$CONFIG_DIR" ]] || { echo "error: agent home directory is unavailable" >&2; exit 1; }
CONFIG_DIR="$CONFIG_DIR/.config/belib-production-cd"
LIBEXEC_DIR="$(getent passwd agent | cut -d: -f6)/.local/libexec/belib-production-cd"
STATE_DIR="$(getent passwd agent | cut -d: -f6)/.local/state/belib-production-cd"
RELEASE_DIR="$(getent passwd agent | cut -d: -f6)/.local/share/belib-production-cd/releases"
UNIT_DIR="$(getent passwd agent | cut -d: -f6)/.config/systemd/user"
CONFIG_PATH="$CONFIG_DIR/config.json"
SERVICE_PATH="$UNIT_DIR/$UNIT_NAME.service"
TIMER_PATH="$UNIT_DIR/$UNIT_NAME.timer"

EXISTING_UNITS=$(systemctl --user list-unit-files --no-legend 2>/dev/null || true)
if [[ "$EXISTING_UNITS" == *"$UNIT_NAME.timer"* ]]; then
  FRAGMENT=$(systemctl --user show -p FragmentPath --value "$UNIT_NAME.timer" 2>/dev/null || true)
  if [[ "$FRAGMENT" == "$TIMER_PATH" ]] && systemctl --user is-active --quiet "$UNIT_NAME.timer"; then
    EXEC_COMMAND=$(systemctl --user show -p ExecStart --value "$UNIT_NAME.service" 2>/dev/null || true)
    if [[ "$EXEC_COMMAND" == *"$LIBEXEC_DIR/kagoya_pull_agent.py"* && "$EXEC_COMMAND" == *"$CONFIG_PATH"* ]]; then
      echo "Existing BeLib production CD timer is already active; no duplicate timer was created."
      exit 0
    fi
    echo "error: the existing timer points to a different dispatcher; it was left unchanged" >&2
    exit 1
  fi
  echo "error: an existing BeLib CD timer requires owner inspection; it was left unchanged" >&2
  exit 1
fi
if grep -Eiq 'belib.*(deploy|production-cd)|(deploy|production-cd).*belib' <<<"$EXISTING_UNITS"; then
  echo "error: an existing BeLib deployment unit was found; reuse it instead of adding a timer" >&2
  exit 1
fi
if [[ -e "$CONFIG_PATH" || -e "$SERVICE_PATH" || -e "$TIMER_PATH" ]]; then
  echo "error: partial dispatcher installation exists; no files were replaced" >&2
  exit 1
fi

CONTAINER_JSON=$(docker inspect "$CONTAINER_NAME" 2>/dev/null) || {
  echo "error: the approved production frontend container was not found" >&2
  exit 1
}
SAFE_METADATA=$(python3 -c '
import json, os, sys
items = json.load(sys.stdin)
if len(items) != 1:
    raise SystemExit("error: production frontend container is not unique")
item = items[0]
labels = item.get("Config", {}).get("Labels") or {}
name = item.get("Name", "").lstrip("/")
if name != "belib-production-frontend-1" or labels.get("com.docker.compose.service") != "frontend":
    raise SystemExit("error: container name/service does not match the production frontend")
project = labels.get("com.docker.compose.project", "")
working_dir = labels.get("com.docker.compose.project.working_dir", "")
files = [part.strip() for part in labels.get("com.docker.compose.project.config_files", "").split(",") if part.strip()]
networks = sorted((item.get("NetworkSettings", {}).get("Networks") or {}).keys())
if not project or not working_dir or not os.path.isdir(working_dir) or len(files) != 1:
    raise SystemExit("error: production Compose labels are missing or use multiple config files")
if os.path.basename(files[0]) != "compose.yaml" or not os.path.isfile(files[0]):
    raise SystemExit("error: the approved production config must be a readable compose.yaml")
if not networks or item.get("State", {}).get("Running") is not True:
    raise SystemExit("error: production frontend is stopped or has no Compose network")
if not item.get("Image") or not item.get("Config", {}).get("Image"):
    raise SystemExit("error: production frontend image identity is incomplete")
print(json.dumps({
  "project": project,
  "working_dir": os.path.realpath(working_dir),
  "config_file": os.path.realpath(files[0]),
  "image_id": item["Image"],
  "image_ref": item["Config"]["Image"],
  "networks": networks,
}, separators=(",", ":")))
' <<<"$CONTAINER_JSON") || exit 1

PROJECT=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["project"])' <<<"$SAFE_METADATA")
IMAGE_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["image_id"])' <<<"$SAFE_METADATA")
PRODUCTION_PROJECT_DIR=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["working_dir"])' <<<"$SAFE_METADATA")
PRODUCTION_COMPOSE_FILE=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["config_file"])' <<<"$SAFE_METADATA")
if ! TRUSTED_COMPOSE_CONFIG=$(env -u VITE_API_BASE_URL docker compose \
  --project-name "$PROJECT" \
  --project-directory "$PRODUCTION_PROJECT_DIR" \
  --file "$PRODUCTION_COMPOSE_FILE" \
  --env-file "$PRODUCTION_ENV_FILE" \
  config --format json 2>/dev/null); then
  echo "error: the approved production Compose config could not be resolved" >&2
  exit 1
fi
EXPECTED_API_BASE_URL=$(python3 -c '
import json, sys
try:
    config = json.load(sys.stdin)
    value = config["services"]["frontend"]["build"]["args"]["VITE_API_BASE_URL"]
except (KeyError, TypeError, json.JSONDecodeError):
    raise SystemExit(1)
if not isinstance(value, str) or not value:
    raise SystemExit(1)
print(value)
' <<<"$TRUSTED_COMPOSE_CONFIG") || {
  echo "error: the approved production API base URL is missing" >&2
  exit 1
}
unset TRUSTED_COMPOSE_CONFIG
ROLLBACK_REF="belib-frontend:rollback-${IMAGE_ID#sha256:}"
ROLLBACK_REF="${ROLLBACK_REF:0:44}"
docker image tag "$IMAGE_ID" "$ROLLBACK_REF" >/dev/null
TAGGED_IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$ROLLBACK_REF" 2>/dev/null) || {
  echo "error: prior frontend image could not be retained" >&2
  exit 1
}
[[ "$TAGGED_IMAGE_ID" == "$IMAGE_ID" ]] || { echo "error: rollback tag does not resolve to the saved image ID" >&2; exit 1; }

mkdir -m 700 -p "$CONFIG_DIR" "$LIBEXEC_DIR" "$STATE_DIR" "$RELEASE_DIR" "$UNIT_DIR"
chmod 700 "$CONFIG_DIR" "$LIBEXEC_DIR" "$STATE_DIR" "$RELEASE_DIR"
install -m 700 "$SCRIPT_DIR/kagoya_pull_agent.py" "$LIBEXEC_DIR/kagoya_pull_agent.py"
install -m 700 "$SCRIPT_DIR/release_gate.py" "$LIBEXEC_DIR/release_gate.py"

BELIB_EXPECTED_API_BASE_URL="$EXPECTED_API_BASE_URL" python3 - "$SAFE_METADATA" "$PRODUCTION_ENV_FILE" "$CONFIG_PATH" "$LIBEXEC_DIR" "$STATE_DIR" "$RELEASE_DIR" "$ROLLBACK_REF" <<'PY'
import json, os, sys
metadata = json.loads(sys.argv[1])
env_file, config_path, libexec, state_dir, release_dir, rollback_ref = sys.argv[2:]
expected_api_base_url = os.environ.get("BELIB_EXPECTED_API_BASE_URL", "")
if not expected_api_base_url:
    raise SystemExit("error: the approved production API base URL is missing")
config = {
    "repository": "hiroyuki9614/book_library",
    "repo_url": "https://github.com/hiroyuki9614/book_library.git",
    "production_container": "belib-production-frontend-1",
    "expected_project": metadata["project"],
    "expected_networks": metadata["networks"],
    "expected_api_base_url": expected_api_base_url,
    "production_env_file": env_file,
    "original_project_dir": metadata["working_dir"],
    "original_compose_file": metadata["config_file"],
    "bootstrap_image_id": metadata["image_id"],
    "bootstrap_rollback_ref": rollback_ref,
    "release_root": release_dir,
    "state_file": os.path.join(state_dir, "state.json"),
    "lock_file": os.path.join(state_dir, "dispatch.lock"),
    "base_url": "http://127.0.0.1:8080",
    "agent_uid": os.getuid(),
}
with open(config_path, "x", encoding="utf-8") as output:
    json.dump(config, output, sort_keys=True)
    output.write("\n")
os.chmod(config_path, 0o600)
PY

PYTHON=$(command -v python3)
cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=BeLib production frontend release dispatcher

[Service]
Type=oneshot
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$STATE_DIR $RELEASE_DIR
WorkingDirectory=$RELEASE_DIR
ExecStart=$PYTHON $LIBEXEC_DIR/kagoya_pull_agent.py --once --config $CONFIG_PATH
EOF
cat >"$TIMER_PATH" <<'EOF'
[Unit]
Description=Poll reviewed BeLib production frontend requests

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
RandomizedDelaySec=30s
Persistent=true
Unit=belib-production-cd.service

[Install]
WantedBy=timers.target
EOF
chmod 600 "$SERVICE_PATH" "$TIMER_PATH"
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME.timer"
systemctl --user is-active --quiet "$UNIT_NAME.timer" || {
  echo "error: the BeLib deployment timer did not become active" >&2
  exit 1
}
echo "BeLib frontend pull dispatcher is installed for project=$PROJECT; no production container was changed."
