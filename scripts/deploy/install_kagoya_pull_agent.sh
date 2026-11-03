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

AGENT_UID=$(id -u)
AGENT_RUNTIME_DIR="/run/user/$AGENT_UID"
python3 - "$AGENT_RUNTIME_DIR" "$AGENT_UID" "${XDG_RUNTIME_DIR:-}" <<'PY'
import os, stat, sys
runtime_dir, uid_text, inherited = sys.argv[1:]
uid = int(uid_text)
if inherited and inherited != runtime_dir:
    raise SystemExit("error: XDG_RUNTIME_DIR does not identify the agent user runtime")
try:
    runtime_stat = os.lstat(runtime_dir)
except OSError:
    raise SystemExit("error: the agent user runtime directory is unavailable") from None
if (
    not stat.S_ISDIR(runtime_stat.st_mode)
    or runtime_stat.st_uid != uid
    or stat.S_IMODE(runtime_stat.st_mode) & 0o077
):
    raise SystemExit("error: the agent user runtime directory has unexpected ownership, type, or permissions")
PY
export XDG_RUNTIME_DIR="$AGENT_RUNTIME_DIR"

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
PUBLIC_DEMO_CREDENTIALS_FILE="$(realpath -m -- "$CONFIG_DIR/public-demo-credentials.json")"
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

SAFE_METADATA=$(python3 "$SCRIPT_DIR/kagoya_compose.py" "$CONTAINER_NAME" "$PRODUCTION_ENV_FILE") || exit 1

PROJECT=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["project"])' <<<"$SAFE_METADATA")
IMAGE_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["image_id"])' <<<"$SAFE_METADATA")
PRODUCTION_PROJECT_DIR=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["project_dir"])' <<<"$SAFE_METADATA")
PRODUCTION_COMPOSE_FILE=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["compose_file"])' <<<"$SAFE_METADATA")
PRODUCTION_COMPOSE_OVERRIDE=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["compose_override_file"])' <<<"$SAFE_METADATA")
EXPECTED_API_BASE_URL=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["expected_api_base_url"])' <<<"$SAFE_METADATA")
EXPECTED_COMPOSE_CONTRACT_SHA256=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["compose_contract_sha256"])' <<<"$SAFE_METADATA")
FRONTEND_BOOTSTRAP_MODE=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["frontend_bootstrap_mode"])' <<<"$SAFE_METADATA")
if [[ "$FRONTEND_BOOTSTRAP_MODE" == legacy-override-update-required ]]; then
  echo "error: dispatcher installation is blocked while the protected frontend override uses the legacy bootstrap shape; have its authorized owner update only that override, run the read-only KAGOYA preflight again, then rerun this installer" >&2
  exit 1
fi
EXPECTED_NETWORKS_JSON=$(python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["networks"], separators=(",", ":")))' <<<"$SAFE_METADATA")
unset SAFE_METADATA
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
install -m 700 "$SCRIPT_DIR/kagoya_compose.py" "$LIBEXEC_DIR/kagoya_compose.py"

BELIB_EXPECTED_API_BASE_URL="$EXPECTED_API_BASE_URL" BELIB_EXPECTED_COMPOSE_CONTRACT_SHA256="$EXPECTED_COMPOSE_CONTRACT_SHA256" BELIB_FRONTEND_BOOTSTRAP_MODE="$FRONTEND_BOOTSTRAP_MODE" python3 - "$PROJECT" "$PRODUCTION_PROJECT_DIR" "$PRODUCTION_COMPOSE_FILE" "$PRODUCTION_COMPOSE_OVERRIDE" "$PRODUCTION_ENV_FILE" "$EXPECTED_NETWORKS_JSON" "$IMAGE_ID" "$CONFIG_PATH" "$LIBEXEC_DIR" "$STATE_DIR" "$RELEASE_DIR" "$ROLLBACK_REF" "$PUBLIC_DEMO_CREDENTIALS_FILE" <<'PY'
import json, os, sys
project, project_dir, compose_file, override_file, env_file, networks_json, image_id, config_path, libexec, state_dir, release_dir, rollback_ref, public_demo_credentials_file = sys.argv[1:]
expected_api_base_url = os.environ.get("BELIB_EXPECTED_API_BASE_URL", "")
expected_contract_sha256 = os.environ.get("BELIB_EXPECTED_COMPOSE_CONTRACT_SHA256", "")
frontend_bootstrap_mode = os.environ.get("BELIB_FRONTEND_BOOTSTRAP_MODE", "")
if not expected_api_base_url or len(expected_contract_sha256) != 64 or frontend_bootstrap_mode not in ("exact-sha", "legacy-override-update-required"):
    raise SystemExit("error: approved production Compose metadata is missing")
config = {
    "repository": "hiroyuki9614/book_library",
    "repo_url": "https://github.com/hiroyuki9614/book_library.git",
    "production_container": "belib-production-frontend-1",
    "expected_project": project,
    "expected_networks": json.loads(networks_json),
    "expected_api_base_url": expected_api_base_url,
    "expected_compose_contract_sha256": expected_contract_sha256,
    "frontend_bootstrap_mode": frontend_bootstrap_mode,
    "production_env_file": env_file,
    "original_project_dir": project_dir,
    "original_compose_file": compose_file,
    "production_compose_override": override_file,
    "bootstrap_image_id": image_id,
    "bootstrap_rollback_ref": rollback_ref,
    "public_demo_credentials_file": public_demo_credentials_file,
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
# PrivateTmp, ProtectSystem, and ReadWritePaths break rootless Docker's UID-owned
# socket visibility; UMask and NoNewPrivileges remain safe.
cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=BeLib production frontend release dispatcher

[Service]
Type=oneshot
UMask=0077
NoNewPrivileges=true
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
