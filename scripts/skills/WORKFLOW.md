# Workflow

## Mandatory Sequence

1. S1 `template_select`
2. S2 `scaffold_verify`
3. S3 `plan_to_contracts`
4. S4 `backend_build`
5. S5 `frontend_bind`
6. S6 `fullstack_test_harness`
7. S7 `triage_and_patch`
8. Pipeline `pipeline` (composes S1→S6 and reports one top-level gate)
9. Template compliance `template_check` (validate template plugin metadata and no-network hooks)
10. Bench `bench` (multi-spec regression sweep)
11. Matrix `matrix` (cross-spec + cross-template compatibility sweep)

## Hard Handoffs

Backend handoff:

- Backend role must update `artifacts/contracts/latest/API_CONTRACT.md` and `api_contract.json` whenever endpoint behavior changes.
- Backend role must treat contract drift as a blocking issue.
- Backend template owners must keep `boot.inventory_cmd` output aligned to schema:
  - `{ "endpoints": [{"method":"GET","path":"/api/resource/{param}"}] }`

Frontend handoff:

- Frontend role binds only to endpoints declared in `artifacts/contracts/latest/API_CONTRACT.md`.
- Mock JSON is allowed only for test fixtures, never production runtime paths.
- Preferred convention: define endpoint wrappers in `lib/apiClient.ts` (or `lib/api.ts` / `src/lib/...`) and call wrappers from route views. S5 uses wrapper parsing as the primary linkage strategy.

## Gates

Contracts gate (S3):

- Acceptance criteria must map to test cases in `TEST_PLAN.md`.
- Layer declarations must be valid (`ui|api|db`).

Harness gate (S6):

- Default mode is no-network for CI/sandbox.
- Optional `--network` mode runs ephemeral-port CRUD checks.
- All non-skipped tests must pass, otherwise gate fails.

Backend coverage gate (S4):

- `backend_build` compares contract endpoints vs discovered backend inventory.
- Missing required endpoints fail the gate; missing optional endpoints warn but pass.

Frontend binding gate (S5):

- Fails when critical routes have no linked contract endpoint usage.
- Fails on runtime mock-data signals outside test directories unless explicitly exempted.
- Exemptions are config-first via `skills/config/exemptions.json` and each exemption must include owner, reason, and expiry.
- Exemption schema fields: `id`, `rule`, `path_or_pattern`, `reason`, `owner`, `expires_on` (YYYY-MM-DD), optional `notes`.
- Expired exemptions generate warnings and can be made blocking with `python -m skills frontend_bind --strict`.

Pipeline gate:

- `pipeline` writes `artifacts/pipeline/latest/pipeline_result.json` as canonical orchestrator input.
- `pipeline --strict` propagates strict policy to S5 (`frontend_bind --strict`).
- `pipeline --triage-on-fail` runs S7 automatically and records triage pointers in `pipeline_result.json`.

Swarm handoff contract:

- `skills/handoff_contract.json` defines machine-readable step order, dependencies, and strict-mode effects.
- `pipeline_result.json` includes `handoff_contract_path` and `handoff_contract_sha256` for orchestrator integrity checks.

Template compliance gate:

- `template_check` validates required template metadata and no-network `test_cmd`.
- Missing recommended `inventory_cmd` warns by default and fails under `--strict`.

## Suggested Swarm Roles

Planner role:

- Runs S1 then S3 first.

Backend role:

- Consumes contracts and runs S4.

Frontend role:

- Consumes contracts and runs S5.

QA/Gatekeeper role:

- Runs S6 and, on failure, S7.

Orchestrator role:

- Runs `pipeline` for fail-fast, single-report gating across all core steps.

## Swarm Integration

Preferred orchestrator input:

- `artifacts/pipeline/latest/pipeline_result.json`

Option A (simple gate):

1. Build/update the app.
2. Run:
   - `python -m skills pipeline --spec <SPEC.md> --orchestrator`
3. Ship only when `overall_status` is `pass`.

Option B (recommended loop):

1. Build/update the app.
2. Run:
   - `python -m skills pipeline --spec <SPEC.md> --triage-on-fail --orchestrator`
3. If `overall_status` is `fail`, read triage pointers in `pipeline_result.json`.
4. Dispatch targeted worker based on classification (`env/bootstrap`, `contract mismatch`, `backend runtime`, `frontend binding`, `db persistence`, `test flakiness`).
5. Re-run pipeline until green.

Machine recipe:

- `skills/swarm_integration_recipe.json` encodes preferred flow commands and next-file pointers.
