---
name: gates-run
description: Run test/lint/type gates inside a Docker image built from a repo profile, capture logs and error signals, and write a structured GateReport. Use when Codex needs to execute gates in containers with timeouts, truncation, and flaky-run detection.
---

# Gates Run

## Overview

Run repo gate commands inside a Docker container, capture structured results and signals, and write a GateReport JSON to `.pf_manifest/gates/`.

## Workflow

1. Provide `repo_dir`, `image_tag`, and `profile_path`.
2. Optionally select `gates_to_run` and `repeats` for flake detection.
3. Run `scripts/gates_run.py` (CLI reads JSON from stdin, writes JSON response to stdout).

## Deterministic Behavior

- Commands come from `profile.gates` (`test`, `lint`, `typecheck`).
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

## Resources

### scripts/
- `gates_run.py`: main runner (library + CLI entrypoint)
- `test_gates_run.py`: integration test (skips if Docker unavailable)
