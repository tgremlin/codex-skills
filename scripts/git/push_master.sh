#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

echo "[push_master] Repository: $repo_root"
git status

if git push origin master; then
  echo "[push_master] Push succeeded. Verify CI in GitHub Actions."
else
  echo "[push_master] Push failed. Check network/auth and retry from a networked shell." >&2
  exit 1
fi
