# Contracts Conventions (S3)

`plan_to_contracts` writes contracts to:

- `artifacts/contracts/<timestamp>/...`
- `artifacts/contracts/latest/...`

## Required Files

Human-readable:

- `API_CONTRACT.md`
- `DATA_MODEL.md`
- `ROUTES.md`
- `TEST_PLAN.md`

Machine-readable:

- `api_contract.json`

Additional:

- `contracts_summary.json`
- `GateReport.md`

## Acceptance Criteria Parsing

The SPEC must define acceptance criteria using one of these headings:

- `## Acceptance Criteria`
- `Acceptance Criteria:`

And then bullet or numbered items, for example:

```md
## Acceptance Criteria
- User can create a todo
- User can complete a todo
- User can delete a todo
```

Each criterion is assigned an ID in order: `AC-001`, `AC-002`, ...

## TEST_PLAN Format

`TEST_PLAN.md` must contain a markdown table with columns:

- `test_id`
- `acceptance_ids`
- `layers`
- `description`

Example:

```md
| test_id | acceptance_ids | layers | description |
|---|---|---|---|
| TC-001-UI | AC-001 | ui | create flow in UI |
| TC-001-API | AC-001 | api | create endpoint contract |
| TC-001-DB | AC-001 | db | row persisted |
```

`acceptance_ids` may contain comma-separated IDs.
`layers` may contain comma-separated values from: `ui`, `api`, `db`.

## Mapping Gate Rules (Hard Fail)

The contracts gate fails if any of these occur:

- no acceptance criteria found in SPEC
- an acceptance criterion maps to zero test rows
- a test row declares no layers
- a test row uses any layer outside `ui|api|db`

On failure, details and next steps are written to `artifacts/contracts/latest/GateReport.md`.
