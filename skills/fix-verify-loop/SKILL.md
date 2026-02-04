---
name: fix-verify-loop
description: Orchestrate a deterministic fix/verify workflow that runs UI smoke, triage on failure, layout audit, and optional visual snapshots, then emits a verification packet with before/after evidence. Use when Codex needs a self-driving loop to diagnose UI failures, apply minimal fixes, and validate recovery.
---

# Fix/Verify Loop

## Overview

Run a self-driving workflow that captures failing evidence, produces triage, applies minimal fixes (by Codex), re-verifies smoke + layout, optionally checks visual snapshots, and writes a final verification packet.

## Runner

- Repo script: `scripts/fix-verify-loop.js`
- Output: `artifacts/fix-verify/<timestamp>/`

## Workflow

### Phase 1 (before)

```bash
node scripts/fix-verify-loop.js
```

- Runs `ui-smoke-artifacts` via `scripts/ui-smoke-docker.sh`.
- If smoke fails: runs `scripts/ui-smoke-triage.py` and stops.
- Writes `artifacts/fix-verify/<id>/before/paths.json` and reports.

### Phase 2 (after)

```bash
FIX_VERIFY_DIR=artifacts/fix-verify/<id> node scripts/fix-verify-loop.js
```

- Re-runs smoke, layout audit, and (optional) visual snapshots.
- Emits `final.md` with evidence and verification summaries.

## Configuration

- `FIX_VERIFY_PHASE=before|after` (optional override)
- `FIX_VERIFY_WITH_SNAPSHOTS=1` to enable visual snapshot gate
- `FIX_VERIFY_SKIP_LAYOUT=1` to skip layout audit
- `FIX_VERIFY_SKIP_SMOKE=1` to skip smoke
- `FIX_VERIFY_SKIP_TRIAGE=1` to skip triage
- `FIX_VERIFY_SMOKE_CMD`, `FIX_VERIFY_LAYOUT_CMD`, `FIX_VERIFY_SNAPSHOT_CMD`, `FIX_VERIFY_TRIAGE_CMD` to override commands
- `UI_SMOKE_DISABLE_SW=0` (or `UI_DISABLE_SW=0`) to allow service workers during smoke

## Outputs

```
artifacts/fix-verify/<timestamp>/
  before/
    paths.json
    ui-smoke-report.md
    triage.md (if smoke failed)
  after/
    paths.json
    ui-smoke-report.md
    layout-mobile-report.md
    layout-desktop-report.md
  final.md
```

## Success Gate

- Smoke passes.
- Layout audit has zero overlaps/spacing issues and close/remove spacing ok (mobile + desktop).
- If snapshots enabled, no diffs.

## Notes

- The loop composes existing skills; it does not modify code itself.
- The “fix” step is performed by Codex and should be minimal (layout spacing, z-index, padding, etc.).
