---
name: ui-layout-visual-audit
description: Run a Playwright-based layout audit across responsive viewports to detect overlapping/too-close actions, clipping, offscreen elements, and badge overlaps using stable data-testid anchors. Use when UI layout regressions need deterministic geometry checks and screenshots.
---

# UI Layout Visual Audit

## Overview

Capture screenshots and DOM geometry across mobile/desktop viewports, then flag overlap, spacing, clipping, offscreen, and badge overlap/proximity violations around key UI elements like drawers/modals.

## Workflow

1. Ensure selector anchors exist.
   - Use `data-testid` values from selector-hardening (`details-drawer`, `drawer-close`, `drawer-remove`, `board-root`, `job-card`).

2. Copy the runner into the repo.
   - `scripts/ui-layout-audit.js`

3. Run the audit.
   - `node scripts/ui-layout-audit.js`

4. Review outputs.
   - `artifacts/ui-layout/<timestamp>/<viewport>/report.md`
   - `overlaps.json`, `spacing.json`, `clipping.json`, `offscreen.json`
   - `badges.json`, `badge_issues.json`
   - `close-remove.json`, `hitbox.json`, `hard_failures.json`
   - screenshots in `screenshots/` (including `*-highlight.png`)

## Configuration

- `UI_MOBILE_VIEWPORT` (default: `390x844`) to override the mobile viewport (e.g., `412x915` for Galaxy S24 Ultra).
- `BASE_URL` (default: `http://localhost:3000`)
- `UI_LAYOUT_PATH` (default: `/routes`)
- `UI_LAYOUT_RUN_ID` (default: timestamp)
- `UI_LAYOUT_OUTPUT` (default: `artifacts/ui-layout/<run>`)
- `UI_LAYOUT_SPACING` (default: `8`)
- `UI_LAYOUT_OVERLAP_TOLERANCE` (default: `1`)
- `UI_LAYOUT_CLOSE_REMOVE_SPACING` (default: `16`)
- `UI_LAYOUT_CLOSE_REMOVE_MIN_SIZE` (default: `44`)
- `UI_LAYOUT_TABLET=1` to include tablet viewport (768x1024)

## Notes

- Deterministic: avoids sleeps, disables animations via injected CSS.
- Uses Playwright to open the drawer by clicking the first `job-card`.
- Dev/test-only SW disable: set `UI_DISABLE_SW=1` (or `UI_SMOKE_DISABLE_SW=1`).
- Badge checks look for small rounded letter badges (e.g. profile initials) or elements with data-testids/labels containing badge/profile/avatar, and flag overlaps or proximity to drawer actions.

## Resources

### scripts/
- `ui-layout-audit.js`: layout audit runner.
