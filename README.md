# Codex Skills: Full-Stack Swarm Skills Pack

This repository provides a deterministic, artifact-first skills pack for building and validating full-stack applications with clear backend/frontend handoffs.

## What This Is

The pack is a local CLI (`python -m skills`) with composable commands that generate contracts, verify implementation coverage, run full-stack checks, and emit machine-readable outputs for orchestrators.

Core goals:

- Deterministic outputs and auditable artifacts
- Explicit API and data handoffs between backend and frontend
- CI-friendly no-network default execution
- Machine-readable gate results for automation

## Current Version

- Release file: `VERSION`
- Changelog: `CHANGELOG.md`
- Compatibility policy: `docs/compat.md`

## Key Entrypoints

- CLI entrypoint: `skills.py` (`python -m skills ...`)
- Runtime package: `swarm_skills/`
- Skills registry and docs: `scripts/skills/`

## Quick Start

```bash
cd /mnt/Storage/Repos/codex-skills
python3 -m skills list
python3 -m skills doctor
python3 -m skills pipeline --spec examples/SPEC.todo.md
```

Machine-first output for orchestrators:

```bash
python3 -m skills pipeline --spec examples/SPEC.todo.md --orchestrator
```

## Easiest Manual Test Flow

If available on your machine, use the home launcher script:

```bash
/home/allan/run_swarm_skills.sh pipeline
/home/allan/run_swarm_skills.sh all
```

This wrapper uses a dedicated release worktree to reduce local workspace drift issues.

## Main Commands

- `doctor`: environment and toolchain checks
- `spec_wizard`: generate a swarm-skills-compatible SPEC from repository scan + wizard answers
- `template_select`: template selection with deterministic rationale
- `scaffold_verify`: boot/smoke verification
- `plan_to_contracts`: generate contracts and enforce acceptance-to-test mapping
- `backend_build`: contract coverage against backend inventory
- `frontend_bind`: frontend endpoint linkage and mock-data gate
- `fullstack_test_harness`: UI/API/DB checks (no-network default)
- `triage_and_patch`: classify failures and produce minimal patch plans
- `pipeline`: composed S1->S6 gate with canonical `pipeline_result.json`
- `template_check`: template metadata/compliance checks
- `bench`: multi-spec benchmark run
- `matrix`: spec-template compatibility matrix
- `prune_artifacts`: artifact retention cleanup

## Important Artifacts

- `artifacts/pipeline/latest/pipeline_result.json`
- `artifacts/contracts/latest/api_contract.json`
- `artifacts/backend/latest/contract_coverage.json`
- `artifacts/frontend/latest/api_usage.json`
- `artifacts/tests/latest/GateReport.md`
- `artifacts/triage/latest/summary_payload.json`

## Integration Contracts

- `skills/handoff_contract.json`
- `skills/swarm_integration_recipe.json`

## Release and CI

- Push and CI checklist: `docs/push_and_ci_checklist.md`
- Workflow details: `scripts/skills/WORKFLOW.md`
- Contract conventions: `scripts/skills/CONTRACTS.md`

## Detailed Operator Guide

See `docs/guide.md` for:

- day-to-day commands
- baseline and stress workflows
- branch/release flow
- troubleshooting workspace drift
