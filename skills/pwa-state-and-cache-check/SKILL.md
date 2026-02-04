---
name: pwa-state-and-cache-check
description: Extend UI smoke artifacts to detect service worker/cache issues (ChunkLoadError, failed JS/CSS chunk requests) and report PWA diagnostics, with a dev/test-only option to disable service workers. Use when diagnosing blank pages or chunk load failures in a PWA/Next.js app.
---

# PWA State and Cache Check

## Overview

Augment the UI smoke artifacts to capture service worker registration state, ChunkLoadError signals, and failed JS/CSS chunk requests. Add report diagnostics and an optional dev/test switch to disable service workers.

## Workflow

1. Update smoke runner diagnostics.
   - Capture service worker registrations via `navigator.serviceWorker.getRegistrations()`.
   - Detect `ChunkLoadError` (console + page errors).
   - Capture failed JS/CSS chunk requests (response >= 400 and requestfailed).
   - Persist a `pwa.json` artifact with the above evidence.

2. Update report formatting.
   - Add a "PWA/Cache diagnostics" section to `report.md`.
   - Include SW status, chunk error count, and failed chunk URLs.

3. Add dev/test-only SW disable option.
   - Use `UI_SMOKE_DISABLE_SW=1` (or `UI_DISABLE_SW=1`) to block service workers in Playwright.
   - Document this in the smoke skill docs and report.

## Expected Outputs

Artifacts in `artifacts/ui-smoke/<timestamp>/` should include:
- `pwa.json` with SW + chunk failure evidence
- Updated `report.md` section for PWA/Cache diagnostics

## Notes

- Default behavior blocks SWs in smoke runs; set `UI_SMOKE_DISABLE_SW=0` (or `UI_DISABLE_SW=0`) to allow SWs when you want to validate cache behavior.
- Prefer minimal changes; keep existing smoke artifacts intact.
- Flag chunk-related failures explicitly with evidence in the report.
- The SW disable toggle is dev/test-only and should not be used in production.
