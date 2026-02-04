# Selector Anchors

## Naming conventions

- Use `data-testid` with kebab-case values (e.g., `board-root`, `drawer-close`).
- Prefer container roots and key actions over deeply nested elements.
- Keep names stable even if copy or layout changes.

## Required anchors

These test IDs must exist and remain stable:

- `dashboard-root` (app main)
- `board-root` (routes board container)
- `board-lane` (route lane container)
- `job-card` (job card root)
- `details-drawer` (job details drawer root)
- `drawer-close` (drawer close X)
- `drawer-remove` (remove job action)

## Usage guidelines

- Apply test IDs on the smallest stable container that reflects the UI state.
- Use the same test ID across repeated items (e.g., lanes, job cards).
- Avoid text-based selectors in tests when a test ID exists.
