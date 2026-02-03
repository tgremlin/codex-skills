---
name: gates-run
description: Run test/lint/type gates inside a Docker image built from a repo profile, capture logs and error signals, and write a structured GateReport. Use when Codex needs to execute gates in containers with timeouts, truncation, and flaky-run detection.
---

# Gates Run

## Overview

Run repo gate commands inside a Docker container, capture structured results and signals, and write a GateReport JSON to `.pf_manifest/gates/`.

## Version

- 0.3.0 (2026-02-02)

## Changes

- Add triage schema version plus root_exception_type/first_error_line and runner env.
- Make gate failure reasons more specific (timeout, runner_missing, test_command_failed).
- Fix triage policy_profile handling.

## Workflow

1. Provide `repo_dir`, `image_tag`, and `profile_path`.
2. Optionally select `gates_to_run` and `repeats` for flake detection.
3. Run `scripts/gates_run.py` (CLI reads JSON from stdin, writes JSON response to stdout).

## Deterministic Behavior

- Commands come from `profile.gates` (`test`, `lint`, `typecheck`).
- If `profile.repo_setup_cmds` is present, run them against the mounted repo before gates and record logs/diff summary and tree hashes.
- If `profile.allow_editable_install=true`, inject `python -m pip install -e .` as a **test gate prelude** (same container/process as tests) and record an import probe in that gate context.
- Idempotency: `profile.repo_setup_idempotency_check` supports `warn` (default), `fail`, or `off`.
- Setup failure: default is fail-early; set `profile.repo_setup_continue_on_failure=true` to override (recorded in report).
- Safety: commands are denylisted by default; set `profile.repo_setup_allow_unsafe=true` to override (recorded in report).
- Logs are truncated deterministically to `max_log_bytes` (head+tail).
- Error signals are extracted via regex from pytest/ruff/mypy outputs.
- Uses `PF_TMP_DIR` if set; otherwise `{repo_dir}/.tmp-test` is created and mounted.

## CLI Example

```bash
python3 scripts/gates_run.py <<'JSON'
{
  "repo_dir": "/path/to/repo",
  "image_tag": "patchfoundry/<profile_id>:latest",
  "profile_path": "/path/to/repo/.pf_manifest/repo_profile.json",
  "gates_to_run": ["test", "lint"],
  "repeats": 2,
  "max_log_bytes": 200000
}
JSON
```

## Artifacts

- GateReport JSON: `{repo_dir}/.pf_manifest/gates/{run_id}.json`
- Raw logs: `{repo_dir}/.pf_manifest/gates/{run_id}.run{n}.{gate}.log`
- Repo setup log: `{repo_dir}/.pf_manifest/gates/{run_id}.setup.log`
- Triage summary: `triage` block in GateReport (schema version, status, failure_class/reason, root_exception_type, first_error_line, actionability, apt_security_mode, repo_setup summary, suggested fixes, security risk flags, runner env, gate runtime info like `gate_python`/`gate_pip`, and base image digest when available)
- Unsupported registry: updates `{repo_dir}/.pf_manifest/unsupported_registry.json` (or `{tmp_root}/bugsinpy/unsupported_registry.json` for BugsInPy) when actionability is `dataset_metadata_issue` or `infra_issue` with `stage=gates_run`.

## Resources

### scripts/
- `gates_run.py`: main runner (library + CLI entrypoint)
- `test_gates_run.py`: integration test (skips if Docker unavailable)
