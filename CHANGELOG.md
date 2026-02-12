# Changelog

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
