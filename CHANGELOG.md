# Changelog

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
