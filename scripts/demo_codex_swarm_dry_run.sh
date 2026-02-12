#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "$0")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

./codex-swarm run \
  --repo . \
  --goal "Demonstrate codex swarm dry-run artifact generation" \
  --dry-run \
  --autofix \
  --max-iterations 1 \
  --gen-spec-if-missing
