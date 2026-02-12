#!/usr/bin/env bash
set -euo pipefail

IMAGE="${UI_SMOKE_IMAGE:-mcr.microsoft.com/playwright:v1.58.1-noble}"
PNPM_VERSION="${UI_SMOKE_PNPM_VERSION:-10.28.2}"
DOCKER_CMD="${UI_DOCKER_CMD:-pnpm exec playwright test e2e/ui-smoke.spec.ts}"

if [[ ! -f "playwright.config.ts" ]]; then
  echo "Run this script from the repo root (playwright.config.ts not found)." >&2
  exit 1
fi

ENV_FLAGS=()
for var in BASE_URL \
  UI_SMOKE_PATH UI_SMOKE_TESTID UI_SMOKE_RUN_ID UI_SMOKE_DEV_AUTH UI_SMOKE_DISABLE_SW UI_DISABLE_SW \
  UI_LAYOUT_PATH UI_LAYOUT_RUN_ID UI_LAYOUT_OUTPUT UI_LAYOUT_SPACING UI_LAYOUT_OVERLAP_TOLERANCE UI_LAYOUT_TABLET \
  UI_SNAPSHOT_PATH UI_SNAPSHOT_UPDATE UI_SNAPSHOT_THRESHOLD \
  UI_MOBILE_VIEWPORT \
  UI_DOCKER_CMD; do
  if [[ -n "${!var:-}" ]]; then
    ENV_FLAGS+=(-e "$var=${!var}")
  fi
done

docker run --rm -t --network=host \
  -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
  -e CI=1 \
  "${ENV_FLAGS[@]}" \
  -v "$PWD":/work \
  -v /work/node_modules \
  -w /work \
  "$IMAGE" \
  bash -lc "corepack enable && corepack prepare pnpm@${PNPM_VERSION} --activate && pnpm install && ${DOCKER_CMD}"
