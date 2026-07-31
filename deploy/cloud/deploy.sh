#!/usr/bin/env bash
# Ship one committed revision to the customer Demo host and run one deployment.
#
#   deploy/cloud/deploy.sh [ssh-target] [commit-ish]
#
# Defaults to root@49.233.213.109 and HEAD. The remote side keeps every secret,
# the PostgreSQL volume and the registered Demo account across runs, so this is
# safe to re-run before a demo.
set -euo pipefail

TARGET="${1:-root@49.233.213.109}"
REVISION="${2:-HEAD}"
REMOTE_ROOT="/opt/automation-tool-demo/src"
DEPLOYABLE_PATHS=(
  .dockerignore
  backend
  contracts/publishing/bilibili-open-api.v1.json
  deploy/cloud
  deploy/postgresql
  deploy/secrets
)
REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "${REPOSITORY_ROOT}"
VCS_REF="$(git rev-parse --verify "${REVISION}^{commit}")"

# The image carries VCS_REF as org.opencontainers.image.revision, so whatever
# ships has to BE that commit's tree. `git archive` guarantees exactly that, and
# it emits tracked content only. Shipping the working tree with macOS tar used
# to smuggle 289 AppleDouble `._*` sidecars along; Alembic globs versions/*.py,
# matched `._20260718_0001_baseline.py`, and died on its null bytes.
DIRTY="$(git status --porcelain -- "${DEPLOYABLE_PATHS[@]}")"
if [[ -n "${DIRTY}" ]]; then
  echo "[deploy.sh] refusing to deploy: the deployable paths differ from ${VCS_REF}." >&2
  echo "[deploy.sh] commit or stash these first, or the image revision label lies:" >&2
  echo "${DIRTY}" >&2
  exit 1
fi

APP_VERSION="$(git show "${VCS_REF}:backend/pyproject.toml" \
  | sed -n 's/^version = "\(.*\)"$/\1/p' | head -1)"

echo "[deploy.sh] target=${TARGET} revision=${VCS_REF} version=${APP_VERSION}"

ssh "${TARGET}" "rm -rf ${REMOTE_ROOT} && mkdir -p ${REMOTE_ROOT}"
git archive --format=tar "${VCS_REF}" "${DEPLOYABLE_PATHS[@]}" \
  | ssh "${TARGET}" "tar -xf - -C ${REMOTE_ROOT}"

ssh "${TARGET}" "python3 ${REMOTE_ROOT}/deploy/cloud/deploy_cloud_demo.py \
  --vcs-ref '${VCS_REF}' --app-version '${APP_VERSION}' ${DEPLOY_EXTRA_ARGS:-}"
