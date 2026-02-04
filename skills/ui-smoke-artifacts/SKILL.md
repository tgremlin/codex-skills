---
name: ui-smoke-artifacts
description: Create a Playwright-based UI smoke runner that visits BASE_URL, navigates a happy path, asserts stable UI anchors, and always writes artifacts (report, console logs, page errors, failed requests, screenshot on failure, trace, plus PWA/cache diagnostics) to artifacts/ui-smoke/TIMESTAMP. Use when adding reproducible UI evidence for web apps or when asked to build a smoke test runner with artifact capture.
---

# UI Smoke Artifacts

## Overview

Create or update a Playwright smoke runner that exercises the primary screen and always emits artifacts for debugging and evidence.

## Workflow

1. Inspect repo for existing Playwright usage and stable anchors.
   - Search for Playwright config/tests, e2e folders, or data-testid attributes.
   - Identify the primary route and any auth gate; prefer a non-auth route or a dev-only bypass.

2. Add Playwright config and dependency if missing.
   - Add `@playwright/test` as a dev dependency and install browsers if the repo does not already use Playwright.
   - Copy `assets/playwright.config.ts` into the repo root if no config exists, or merge with existing config with minimal changes.

3. Add UI smoke runner.
   - Copy `assets/ui-smoke.spec.ts` to `e2e/ui-smoke.spec.ts` (or the repo's test directory).
   - Update the primary anchor selector (prefer data-testid; set `UI_SMOKE_TESTID` when needed).
   - Set `UI_SMOKE_PATH` or adjust the default path to the main screen.
   - If auth is required, implement a dev-only bypass (localStorage/cookies) and label it clearly as dev-only. Use the template's `UI_SMOKE_DEV_AUTH` hook or replace it with a repo-specific dev flow.

4. Ensure artifact output format.
   - Verify artifacts are written to `artifacts/ui-smoke/<timestamp>/` every run.
   - Confirm `report.md`, `console.json`, `pageerrors.json`, `network.json`, `pwa.json`, and `trace.zip` are always present.
   - Screenshot should be captured on failure.
   - `report.md` should include a "PWA/Cache diagnostics" section (service worker status, ChunkLoadError occurrences, and JS/CSS chunk failures).

5. Prefer container execution when host sandboxing blocks Playwright.
   - Copy `assets/ui-smoke-docker.sh` into the repo (e.g., `scripts/ui-smoke-docker.sh`).
   - Run it from the repo root to avoid host sandbox/user-namespace failures.
   - Override image/pnpm via `UI_SMOKE_IMAGE` and `UI_SMOKE_PNPM_VERSION` env vars when needed.
   - Use `UI_DOCKER_CMD` to run other Playwright-based scripts (for example, `node scripts/ui-layout-audit.js`).

## Implementation Notes

- Use Playwright auto-waits + `expect()`; avoid fixed sleeps.
- Treat `BASE_URL` as the source of truth; allow `UI_SMOKE_PATH` to override route.
- Keep the report succinct with steps attempted, expected vs actual, and summary counts.
- SWs are blocked by default in smoke runs. Set `UI_SMOKE_DISABLE_SW=0` (or `UI_DISABLE_SW=0`) to allow service workers, or `...=1` to force block.

## Resources

### assets/
- `playwright.config.ts`: minimal Playwright config template.
- `ui-smoke.spec.ts`: smoke runner template with artifact capture, PWA/cache diagnostics, and dev-only auth hook.
- `ui-smoke-docker.sh`: containerized runner to execute Playwright in locked-down environments.
