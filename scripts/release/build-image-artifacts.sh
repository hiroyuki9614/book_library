#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  scripts/release/build-image-artifacts.sh FULL_SOURCE_REVISION \
    --backend-dockerfile PATH --frontend-dockerfile PATH \
    --frontend-api-base-url URL \
    [--backend-context PATH] [--frontend-context PATH] \
    [--output-root PATH]

The revision must be the complete hexadecimal revision of the current checkout.
EOF
  exit 2
}

[[ $# -ge 1 ]] || usage
SOURCE_REVISION=$1
shift
[[ "$SOURCE_REVISION" =~ ^[0-9a-fA-F]{40,64}$ ]] || { echo "error: FULL_SOURCE_REVISION must be a complete 40- or 64-character hexadecimal revision" >&2; exit 1; }
SOURCE_REVISION=${SOURCE_REVISION,,}
BACKEND_DOCKERFILE=''
FRONTEND_DOCKERFILE=''
FRONTEND_API_BASE_URL=''
BACKEND_CONTEXT='backend'
FRONTEND_CONTEXT='frontend'
OUTPUT_ROOT='release-artifacts'

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend-dockerfile) [[ $# -ge 2 ]] || usage; BACKEND_DOCKERFILE=$2; shift 2 ;;
    --frontend-dockerfile) [[ $# -ge 2 ]] || usage; FRONTEND_DOCKERFILE=$2; shift 2 ;;
    --frontend-api-base-url) [[ $# -ge 2 ]] || usage; FRONTEND_API_BASE_URL=$2; shift 2 ;;
    --backend-context) [[ $# -ge 2 ]] || usage; BACKEND_CONTEXT=$2; shift 2 ;;
    --frontend-context) [[ $# -ge 2 ]] || usage; FRONTEND_CONTEXT=$2; shift 2 ;;
    --output-root) [[ $# -ge 2 ]] || usage; OUTPUT_ROOT=$2; shift 2 ;;
    *) echo "error: unknown option: $1" >&2; usage ;;
  esac
done
[[ -n "$BACKEND_DOCKERFILE" && -n "$FRONTEND_DOCKERFILE" ]] || { echo "error: both production Dockerfile paths are required" >&2; exit 1; }
[[ -n "$FRONTEND_API_BASE_URL" && "$FRONTEND_API_BASE_URL" != *$'\n'* && "$FRONTEND_API_BASE_URL" != *$'\r'* ]] || { echo "error: --frontend-api-base-url is required and must not contain newlines" >&2; exit 1; }
command -v git >/dev/null || { echo "error: git is required" >&2; exit 1; }
command -v docker >/dev/null || { echo "error: docker is required" >&2; exit 1; }
command -v sha256sum >/dev/null || { echo "error: sha256sum is required" >&2; exit 1; }
command -v realpath >/dev/null || { echo "error: realpath is required" >&2; exit 1; }

REPOSITORY_ROOT=$(git rev-parse --show-toplevel)
cd "$REPOSITORY_ROOT"
ACTUAL_REVISION=$(git rev-parse HEAD)
[[ "$ACTUAL_REVISION" == "$SOURCE_REVISION" ]] || { echo "error: requested revision does not equal the current checkout HEAD" >&2; exit 1; }

validate_path() {
  local path=$1
  [[ "$path" != /* && "$path" != *$'\n'* && "$path" != *$'\r'* && "$path" != *'"'* && "$path" != *'\\'* ]] || { echo "error: paths must be safe repository-relative values" >&2; exit 1; }
}
for path in "$BACKEND_DOCKERFILE" "$FRONTEND_DOCKERFILE" "$BACKEND_CONTEXT" "$FRONTEND_CONTEXT"; do validate_path "$path"; done

inside_repository() {
  local resolved=$1
  [[ "$resolved" == "$REPOSITORY_ROOT"/* || "$resolved" == "$REPOSITORY_ROOT" ]]
}
BACKEND_DOCKERFILE_REAL=$(realpath -e -- "$BACKEND_DOCKERFILE")
FRONTEND_DOCKERFILE_REAL=$(realpath -e -- "$FRONTEND_DOCKERFILE")
BACKEND_CONTEXT_REAL=$(realpath -e -- "$BACKEND_CONTEXT")
FRONTEND_CONTEXT_REAL=$(realpath -e -- "$FRONTEND_CONTEXT")
for path in "$BACKEND_DOCKERFILE_REAL" "$FRONTEND_DOCKERFILE_REAL" "$BACKEND_CONTEXT_REAL" "$FRONTEND_CONTEXT_REAL"; do inside_repository "$path" || { echo "error: Dockerfile or context escapes the repository" >&2; exit 1; }; done
[[ -f "$BACKEND_DOCKERFILE_REAL" && -f "$FRONTEND_DOCKERFILE_REAL" && -d "$BACKEND_CONTEXT_REAL" && -d "$FRONTEND_CONTEXT_REAL" ]] || { echo "error: Dockerfile or context not found" >&2; exit 1; }

check_clean_context() {
  local context=$1
  git diff --quiet HEAD -- "$context" || { echo "error: tracked modifications exist in build context $context" >&2; exit 1; }
  while IFS= read -r -d '' item; do
    [[ -L "$item" ]] && { echo "error: symlink in build context: $item" >&2; exit 1; }
    local relative=${item#"$REPOSITORY_ROOT"/}
    git ls-files --error-unmatch -- "$relative" >/dev/null 2>&1 || { echo "error: untracked file in build context: $relative" >&2; exit 1; }
  done < <(find -P "$context" -type f -print0)
}
check_clean_context "$BACKEND_CONTEXT_REAL"
check_clean_context "$FRONTEND_CONTEXT_REAL"
git diff --quiet HEAD -- "$BACKEND_DOCKERFILE" "$FRONTEND_DOCKERFILE" || { echo "error: tracked Dockerfile modifications exist" >&2; exit 1; }

CI_IDENTIFIER=${CI_RUN_ID:-${GITHUB_RUN_ID:-local}}
[[ "$CI_IDENTIFIER" =~ ^[A-Za-z0-9._:-]+$ ]] || { echo "error: CI_RUN_ID contains unsupported characters" >&2; exit 1; }
if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then BUILD_TIMESTAMP=$(date -u -d "@$SOURCE_DATE_EPOCH" +%Y-%m-%dT%H:%M:%SZ); else BUILD_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ); fi
OUTPUT_DIR="$OUTPUT_ROOT/$SOURCE_REVISION"
mkdir -p "$OUTPUT_DIR"
BACKEND_TAG="belib-backend:$SOURCE_REVISION"
FRONTEND_TAG="belib-frontend:$SOURCE_REVISION"
FRONTEND_API_BASE_URL_SHA256=$(printf '%s' "$FRONTEND_API_BASE_URL" | sha256sum | awk '{print $1}')

docker build --file "$BACKEND_DOCKERFILE_REAL" --tag "$BACKEND_TAG" --label "org.opencontainers.image.revision=$SOURCE_REVISION" --label "org.opencontainers.image.created=$BUILD_TIMESTAMP" --label "org.opencontainers.image.source-revision=$SOURCE_REVISION" "$BACKEND_CONTEXT_REAL"
docker build --file "$FRONTEND_DOCKERFILE_REAL" --build-arg "VITE_API_BASE_URL=$FRONTEND_API_BASE_URL" --tag "$FRONTEND_TAG" --label "org.opencontainers.image.revision=$SOURCE_REVISION" --label "org.opencontainers.image.created=$BUILD_TIMESTAMP" --label "org.opencontainers.image.source-revision=$SOURCE_REVISION" "$FRONTEND_CONTEXT_REAL"
docker image inspect "$BACKEND_TAG" >/dev/null
docker image inspect "$FRONTEND_TAG" >/dev/null
BACKEND_ID=$(docker image inspect --format '{{.Id}}' "$BACKEND_TAG")
FRONTEND_ID=$(docker image inspect --format '{{.Id}}' "$FRONTEND_TAG")
docker save --output "$OUTPUT_DIR/backend.docker.tar" "$BACKEND_TAG"
docker save --output "$OUTPUT_DIR/frontend.docker.tar" "$FRONTEND_TAG"
BACKEND_CHECKSUM=$(sha256sum "$OUTPUT_DIR/backend.docker.tar" | awk '{print $1}')
FRONTEND_CHECKSUM=$(sha256sum "$OUTPUT_DIR/frontend.docker.tar" | awk '{print $1}')
BACKEND_DOCKERFILE_CHECKSUM=$(sha256sum "$BACKEND_DOCKERFILE_REAL" | awk '{print $1}')
FRONTEND_DOCKERFILE_CHECKSUM=$(sha256sum "$FRONTEND_DOCKERFILE_REAL" | awk '{print $1}')
BACKEND_CONTEXT_ID=$(git ls-tree -r HEAD -- "$BACKEND_CONTEXT" | sha256sum | awk '{print $1}')
FRONTEND_CONTEXT_ID=$(git ls-tree -r HEAD -- "$FRONTEND_CONTEXT" | sha256sum | awk '{print $1}')
cat > "$OUTPUT_DIR/manifest.json" <<EOF
{
  "sourceCommit": "$SOURCE_REVISION",
  "buildTimestamp": "$BUILD_TIMESTAMP",
  "ciRunIdentifier": "$CI_IDENTIFIER",
  "images": {
    "backend": {"localImmutableTag": "$BACKEND_TAG", "imageId": "$BACKEND_ID", "dockerfile": "$BACKEND_DOCKERFILE", "dockerfileSha256": "$BACKEND_DOCKERFILE_CHECKSUM", "context": "$BACKEND_CONTEXT", "contextTreeSha256": "$BACKEND_CONTEXT_ID", "archive": "backend.docker.tar", "archiveSha256": "$BACKEND_CHECKSUM"},
    "frontend": {"localImmutableTag": "$FRONTEND_TAG", "imageId": "$FRONTEND_ID", "dockerfile": "$FRONTEND_DOCKERFILE", "dockerfileSha256": "$FRONTEND_DOCKERFILE_CHECKSUM", "context": "$FRONTEND_CONTEXT", "contextTreeSha256": "$FRONTEND_CONTEXT_ID", "archive": "frontend.docker.tar", "archiveSha256": "$FRONTEND_CHECKSUM", "buildConfiguration": {"viteApiBaseUrlSha256": "$FRONTEND_API_BASE_URL_SHA256"}}
  }
}
EOF
sha256sum "$OUTPUT_DIR/manifest.json" > "$OUTPUT_DIR/manifest.json.sha256"
printf 'Artifacts written to %s\n' "$OUTPUT_DIR"
