---
name: bugsinpy-adapter
description: Resolve BugsInPy bug instances to real upstream project checkouts with explicit install/test commands and provenance. Use when Codex needs to integrate BugsInPy into the repo-profile/build/gates pipeline without treating bugs/id as a repo root.
---

# BugsInPy Adapter

## Overview

Resolve a BugsInPy project+bug to a concrete upstream checkout, with explicit install and test commands taken from BugsInPy metadata/scripts.

## Version

- 0.2.0 (2026-02-02)

## Changes

- Add metadata-only candidate picker for non-pytest selection with safe stop when top picks are a single project.

## Workflow

1. Provide `bugsinpy_root`, `project_name`, `bug_id`, and `variant`.
2. Run `scripts/bugsinpy_adapter.py` (CLI reads JSON from stdin, writes JSON response to stdout).
3. Use `resolved_project_dir`, `install_cmds`, `repo_setup_cmds`, `test_cmds`, and `env` as overrides for profile/build/gates.

## Deterministic Rules

- Uses `framework/bin/bugsinpy-checkout` to create a clean checkout under `{PF_TMP_DIR or <bugsinpy_root>/.tmp-test}/bugsinpy/<project>/<bug>/<variant>/`.
- Parses `bug.info`, `bugsinpy_requirements.txt`, `bugsinpy_run_test.sh`, and optional `bugsinpy_setup.sh`.
- Test commands are read from `bugsinpy_run_test.sh`; do not rely on implicit pytest discovery.
- Hard requirement: never treat `bugs/<id>` as a repo root.

## CLI Example

```bash
python3 scripts/bugsinpy_adapter.py <<'JSON'
{
  "bugsinpy_root": "/mnt/Storage/Repos/BugsInPy",
  "project_name": "black",
  "bug_id": "4",
  "variant": "buggy"
}
JSON
```

## Output

- `resolved_project_dir`: checkout root
- `install_cmds`: explicit commands (requirements install)
- `repo_setup_cmds`: commands from `bugsinpy_setup.sh` to run against the mounted repo before gates
- `test_cmds`: explicit test commands from BugsInPy
- `env`: derived env (e.g., PYTHONPATH)
- `provenance`: project, bug, commits, etc.

## Resources

### scripts/
- `bugsinpy_adapter.py`: adapter entrypoint
- `test_bugsinpy_adapter.py`: unit tests for parsing logic
- `bugsinpy_candidate_picker.py`: optional metadata-only picker for non-pytest candidates; prints top candidates and stops if all top picks are the same project (e.g., black)
