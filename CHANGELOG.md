# Changelog

## v2.0.0

Major release introducing Codex-only team mode swarm execution and global CLI migration.

Highlights:

- Added new multi-agent swarm runner (`codex-swarm`) with:
  - plan -> expert selection -> parallel execution -> deterministic integration -> gate -> retry loop
  - required `SecurityExpert` + `TestingExpert` and optional specialist routing
  - bounded retries (`--max-iterations`, `--max-experts`, `--time-budget`, `--max-diff-lines`)
  - deterministic run artifacts under `artifacts/swarm_run/<timestamp>/...`
- Added deterministic spec resolution for swarm mode:
  - provided override (`--spec`)
  - ordered discovery
  - missing-spec fail-fast exit `2` with actionable guidance
  - optional generation (`--gen-spec-if-missing`) and `gen-spec` command
- Added dry-run simulation mode for reproducible end-to-end orchestration checks.
- Added docs and tests for selection, routing, integration strategy, and spec behavior.
- Updated global install flow to install `codex-swarm` and remove legacy `swarm-skills` link.

## v1.1.0

Minor release adding SPEC generation and deterministic SPEC discovery improvements.

Highlights:

- Added `spec_wizard` command:
  - deterministic repo scan evidence
  - optional Flow-Next import and trace mapping
  - generated SPEC + machine artifacts under `artifacts/spec_wizard/latest/*`
- Added deterministic SPEC auto-discovery for spec-consuming commands (`pipeline`, `plan_to_contracts`) when `--spec` is omitted:
  - pointer support via `.swarm/spec_path.txt` or `.swarm/spec.json`
  - deterministic candidate search order under workspace root
  - safety-first hard fail on ambiguity with candidate list + guidance
  - structured JSON/orchestrator error payload on discovery failures
- Expanded tests:
  - `tests/test_spec_wizard_*`
  - `tests/test_spec_discovery.py`
  - `tests/test_cli_spec_omitted.py`
- Updated workflow/operator docs for spec wizard and auto-discovery usage.

## v1.0.0

Initial stable release boundary for the Full-Stack App Swarm Skills Pack.

Highlights:

- Canonical pipeline machine output: `artifacts/pipeline/latest/pipeline_result.json` (`schema_version: 1.0`).
- Contract and gate machine outputs stabilized at schema `1.0`:
  - contracts `api_contract.json`
  - backend `contract_coverage.json`
  - frontend `api_usage.json` and `mock_data_report.json`
  - triage `summary_payload.json` and `summary.json`
- Added quality and operations commands:
  - `template_check`
  - `bench`
  - `matrix`
  - `pipeline --triage-on-fail`
- CI merge gate runs:
  - `python -m skills doctor`
  - `python -m skills pipeline --spec examples/SPEC.todo.md`
