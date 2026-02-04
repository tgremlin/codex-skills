---
name: ui-regression-snapshots
description: Create small Playwright-driven visual regression snapshots (board + drawer states) with baseline images stored in-repo and diff output on mismatch. Use when Codex needs a lightweight, local visual regression check without external services.
---

# UI Regression Snapshots

## Overview

Capture two golden UI states (board loaded + details drawer open), compare against baseline images, and write diffs on mismatch.

## Workflow

1. Copy the runner into the repo.
   - `scripts/ui-regression-snapshots.js`

2. Ensure baseline folders exist (committed to repo).
   - `tests/visual/baseline/<viewport>/<state>.png`

3. Run snapshot comparison.
   - `node scripts/ui-regression-snapshots.js`

4. Update baselines intentionally (when UI changes are expected).
   - `UI_SNAPSHOT_UPDATE=1 node scripts/ui-regression-snapshots.js`

## Output

- Baselines: `tests/visual/baseline/<viewport>/<state>.png`
- Diffs (on failure): `tests/visual/diffs/<viewport>/<state>-diff.png`

States:
- `board`
- `drawer`

Viewports:
- `mobile` (390x844)
- `desktop` (1280x720)

## Configuration

- `UI_MOBILE_VIEWPORT` (default: `390x844`) to override the mobile viewport (e.g., `412x915`).
- `BASE_URL` (default: `http://localhost:3000`)
- `UI_SNAPSHOT_PATH` (default: `/routes`)
- `UI_SNAPSHOT_UPDATE=1` to update baselines
- `UI_SNAPSHOT_THRESHOLD` (default: `0.1`) for pixelmatch sensitivity

## Notes

- Dev/test-only SW disable: set `UI_DISABLE_SW=1` (or `UI_SMOKE_DISABLE_SW=1`).
- Disables animations and uses deterministic waits (testid anchors).
- Requires stable `data-testid` anchors: `board-root`, `job-card`, `details-drawer`, `drawer-close`.
- Diffs are written only when mismatches occur.

## Resources

### scripts/
- `ui-regression-snapshots.js`: baseline + diff runner.
