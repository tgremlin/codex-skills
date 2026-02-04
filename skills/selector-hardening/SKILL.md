---
name: selector-hardening
description: Harden UI selectors by adding stable data-testid anchors (kebab-case) in key containers and actions, documenting them, and updating ui-smoke-artifacts to use those test IDs. Use when Playwright selectors are brittle, geometry audits need stable anchors, or drawer/modal actions need reliable selectors.
---

# Selector Hardening

## Overview

Add minimal, stable `data-testid` anchors and document them so UI automation and geometry audits are resilient to copy/layout changes.

## Workflow

1. Locate the primary screens and interaction points.
   - Identify dashboard root, board root, lanes, job cards, and drawer actions.
   - Prefer container roots and primary actions; avoid deep text-based selectors.

2. Add or confirm `data-testid` anchors.
   - Use kebab-case (e.g., `board-root`, `drawer-close`).
   - Add only what is needed for robustness, especially around drawer/modal actions.

3. Document selectors.
   - Copy `assets/selectors.md` to `docs/testing/selectors.md`.
   - Keep the required anchors list in sync with UI changes.

4. Update smoke tests.
   - Ensure `ui-smoke-artifacts` uses test IDs (default `UI_SMOKE_TESTID=board-root`).
   - Update any repo-level smoke spec to use `page.getByTestId()`.

## Required anchors

- `dashboard-root`
- `board-root`
- `board-lane`
- `job-card`
- `details-drawer`
- `drawer-close`
- `drawer-remove`

## Resources

### assets/
- `selectors.md`: template for `docs/testing/selectors.md`.
