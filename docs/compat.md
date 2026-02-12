# Compatibility Contract

This document defines public machine interfaces for orchestrators and schema evolution rules.

## Public Machine Interfaces

- `artifacts/pipeline/latest/pipeline_result.json`
- `artifacts/contracts/latest/api_contract.json`
- `artifacts/backend/latest/contract_coverage.json`
- `artifacts/frontend/latest/api_usage.json`
- `artifacts/frontend/latest/mock_data_report.json`
- `artifacts/template_check/latest/report.json`
- `artifacts/bench/latest/bench_results.json`
- `artifacts/matrix/latest/matrix.json`
- `artifacts/triage/latest/summary_payload.json`
- `artifacts/triage/latest/summary.json`
- `skills/handoff_contract.json`

## Schema Version Policy

All public machine interfaces include `schema_version`.

Bump rules:

- Major bump: breaking changes (field removal/rename, type changes, required semantic changes).
- Minor bump: additive backward-compatible fields or optional sections.
- Patch release: no machine schema changes (docs/tests/internal only).

## Orchestrator Guidance

- Treat unknown additive fields as ignorable.
- Fail closed on missing required fields for the expected schema major version.
- Use `skills/handoff_contract.json` and `pipeline_result.json` as the primary handoff map.
