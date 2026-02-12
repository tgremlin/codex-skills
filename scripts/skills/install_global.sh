#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "$0")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
SRC="$SCRIPT_DIR/swarm-skills"
TARGET_DIR="${HOME}/.local/bin"
TARGET="$TARGET_DIR/swarm-skills"

mkdir -p "$TARGET_DIR"
ln -sf "$SRC" "$TARGET"

echo "Installed: $TARGET"
echo "If needed, add to PATH: export PATH=\"$HOME/.local/bin:$PATH\""
