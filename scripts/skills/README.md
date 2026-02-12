# Full-Stack App Swarm Skills Pack

This directory documents the repo-local swarm skills pipeline and command registry.

## Why This Lives Under `scripts/skills`

`codex-skills` already uses top-level `skills/` for Codex skill definitions. The application swarm pack is therefore implemented as:

- CLI entrypoint: `python -m skills` (via `skills.py`)
- Python package: `swarm_skills/`
- Operational docs + registry: `scripts/skills/`

## Pattern Inspiration (No Code Copy)

- FullStack-Agent: role separation and explicit handoffs
- FullStack-Dev: template-driven scaffold flow
- FullStack-Bench: end-to-end checks across UI, API, and DB
- FullStack-Learn / paper: structured artifacts and reproducible evaluation

## Registry-Driven Commands

List commands from source-of-truth registry:

```bash
python3 -m skills list
python3 -m skills list --json
```

Command help includes registry-declared required flags and produced artifacts.

## Current Implemented Commands

- `python3 -m skills doctor`
- `python3 -m skills template_select --spec examples/SPEC.todo.md`
- `python3 -m skills scaffold_verify --template local-node-http-crud --port auto`
- `python3 -m skills plan_to_contracts --spec examples/SPEC.todo.md`
- `python3 -m skills backend_build --contracts artifacts/contracts/latest/api_contract.json`
- `python3 -m skills frontend_bind --contracts-dir artifacts/contracts/latest`
- `python3 -m skills fullstack_test_harness --template local-node-http-crud`
- `python3 -m skills triage_and_patch --gate-report artifacts/tests/latest/GateReport.md`
- `python3 -m skills pipeline --spec examples/SPEC.todo.md [--strict] [--triage-on-fail]`
- `python3 -m skills template_check --all [--strict]`
- `python3 -m skills bench --spec-dir examples/specs [--strict] [--network] [--append-history]`
- `python3 -m skills matrix --spec-dir examples/specs [--templates all|id1,id2] [--strict] [--network] [--limit N]`

Optional local network mode:

- `python3 -m skills fullstack_test_harness --template local-node-http-crud --network`

## Deterministic Artifact Standard

Each command writes:

- `artifacts/<skill>/<timestamp>/...`
- `artifacts/<skill>/latest/...` (copy of latest run)
- `summary.json` with stable keys:
  - `skill`, `status`, `started_at`, `ended_at`, `notes`, `artifacts`
  - provenance: `repo_commit`, `python_version`, `node_version`, `template_id`, `template_version`

Compatibility copies:

- S1: `artifacts/plan/template_choice.json`, `artifacts/plan/runbook.md`
- S2: `artifacts/bootstrap/smoke.json`, `artifacts/bootstrap/env.example`
- S3: `artifacts/contracts/latest/*`
- S4: `artifacts/backend/latest/*`
- S5: `artifacts/frontend/latest/*`
- S6: `artifacts/tests/latest/*`
- S7: `artifacts/triage/latest/*`
- Pipeline: `artifacts/pipeline/latest/*`

## Machine Schemas

Machine-readable artifacts include explicit schema versions:

- `artifacts/pipeline/latest/pipeline_result.json` (`schema_version`)
- `artifacts/contracts/latest/api_contract.json` (`schema_version`)
- `artifacts/bench/latest/bench_results.json` (`schema_version`)
- `artifacts/template_check/latest/report.json` (`schema_version`)
- `artifacts/matrix/latest/matrix.json` (`schema_version`)
- `artifacts/backend/latest/contract_coverage.json` (`schema_version`)
- `artifacts/frontend/latest/api_usage.json` (`schema_version`)
- `artifacts/frontend/latest/mock_data_report.json` (`schema_version`)
- `artifacts/triage/latest/summary_payload.json` (`schema_version`)
- `artifacts/triage/latest/summary.json` (`schema_version`)
- `skills/handoff_contract.json` (`schema_version`)

Schema bump policy:

- Major version bump for breaking field removals/renames/type changes.
- Minor version bump for additive, backward-compatible fields.
- Patch changes are documentation/test-only and do not alter schema shape.

Swarm orchestrator contract:

- `skills/handoff_contract.json` defines ordered step handoffs, required inputs, produced outputs, and strict-mode effects.
- `pipeline_result.json` includes:
  - `handoff_contract_path`
  - `handoff_contract_sha256`

## Quick Start

```bash
cd /mnt/Storage/Repos/codex-skills
python3 -m skills doctor
python3 -m skills template_select --spec examples/SPEC.todo.md
python3 -m skills scaffold_verify --template local-node-http-crud --port auto
python3 -m skills plan_to_contracts --spec examples/SPEC.todo.md
python3 -m skills backend_build --contracts artifacts/contracts/latest/api_contract.json
python3 -m skills frontend_bind --contracts-dir artifacts/contracts/latest
python3 -m skills fullstack_test_harness --template local-node-http-crud
python3 -m skills pipeline --spec examples/SPEC.todo.md
python3 -m skills template_check --all
python3 -m skills bench --spec-dir examples/specs
python3 -m skills bench --spec-dir examples/specs --append-history
python3 -m skills matrix --spec-dir examples/specs --templates all --limit 12
```

## Demo Pipeline (Fail Fast)

```bash
./scripts/demo_fullstack_mvp.sh
```

Outputs:

- `artifacts/demo/<timestamp>/exit_codes.json`
- `artifacts/demo/<timestamp>/GateReport.md`
- `artifacts/demo/latest/*`

## CI Merge Gate

Canonical merge gate workflow: `.github/workflows/merge-gate.yml`

CI commands:

```bash
python -m skills doctor
python -m skills pipeline --spec examples/SPEC.todo.md
```

Artifact upload on failure includes:

- `artifacts/pipeline/latest/*`
- `artifacts/**/latest/*`

Inspect each failing step through `artifacts/pipeline/latest/GateReport.md`, then open the referenced per-skill summaries and GateReports.

`pipeline` always writes machine-readable `artifacts/pipeline/latest/pipeline_result.json` for orchestrator ingestion.

Optional stricter local gate:

```bash
python3 -m skills pipeline --spec examples/SPEC.todo.md --strict
```

Optional automatic triage on failure:

```bash
python3 -m skills pipeline --spec examples/SPEC.todo.md --triage-on-fail
```

Bench trend logging:

```bash
python3 -m skills bench --spec-dir examples/specs --append-history
```

History entries are appended to `artifacts/bench/history.jsonl`.

## Any-Folder Use

Use explicit workspace root from another directory:

```bash
python3 /mnt/Storage/Repos/codex-skills/skills.py template_select \
  --workspace-root /mnt/Storage/Repos/codex-skills \
  --spec examples/SPEC.todo.md
```

Install wrapper command for global use:

```bash
./scripts/skills/install_global.sh
swarm-skills template_select --spec examples/SPEC.todo.md
```

## New Active Template

- `templates/nextjs-prisma-sqlite-crud` is an additional active baseline with:
  - deterministic no-network test command (`node scripts/no_network_check.js`)
  - backend inventory command (`node scripts/inventory.js`)
  - scaffold health strategies for sandbox/local resilience

## Template Requirements

Template plugin manifests (`templates/<id>/template.json`) should include:

- Required:
  - `id`, `name`, `version`
  - `capabilities` as an array of capability strings
  - `boot.health_strategy` containing at least one `test_cmd:...` entry
- Recommended:
  - `boot.inventory_cmd` for deterministic backend endpoint inventory

Run compliance checks:

```bash
python3 -m skills template_check --all
python3 -m skills template_check --all --strict
```

## Inventory Schema

`boot.inventory_cmd` should emit deterministic JSON to stdout:

```json
{
  "endpoints": [
    {"method": "GET", "path": "/api/todos"},
    {"method": "PUT", "path": "/api/todos/{param}"}
  ]
}
```

## Deterministic Assumptions

- `backend_build` prefers validated template `inventory_cmd` output when declared; otherwise it falls back to local OpenAPI/static scanning.
- `frontend_bind` prefers API wrapper extraction from `lib/apiClient.ts` / `lib/api.ts` (or `src/lib/...`) and falls back to heuristic endpoint scanning when wrappers are missing.
- Exemptions default to `skills/config/exemptions.json` with required owner/reason/expiry metadata.
