---
name: repo-profile-detect
description: Deterministically detect and emit a Python repository execution profile (install commands, gates, python target, env, working dir) from a checked-out repo. Use when Codex needs to inspect a local repo directory and produce a machine-readable profile for container runners or CI-like execution.
---

# Repo Profile Detect

## Overview

Detect a Python repo's install mechanism and gate commands, then write a stable profile JSON to `.pf_manifest/repo_profile.json` (atomic write).

## Workflow

1. Build a `RepoProfileRequest` with `repo_dir`, optional `explicit_python_version`, and optional `overrides`.
2. Run `scripts/repo_profile_detect.py` (CLI reads JSON from stdin, writes JSON response to stdout).
3. Use the response `profile_id`, `profile`, `profile_runtime`, and `profile_path` for downstream container runner skills.

## Detection Rules (Deterministic)

- **Install precedence:**
  - If `pyproject.toml` and `uv.lock` exist -> use `uv`.
  - Else if `pyproject.toml` and `poetry.lock` exist -> use `poetry`.
  - Else if `requirements.txt` exists -> use `pip -r`.
  - Else if `pyproject.toml` or `setup.py` or `setup.cfg` exists -> use `pip -e .`.
  - Else -> status `unsupported` with reason.
- **Gates:**
  - **tests:** `pytest` if pytest dependency is detected or `tests/` exists.
  - **lint:** `ruff check .` if ruff config exists or ruff dependency is detected.
  - **typecheck:** `mypy .` if mypy config exists or mypy dependency is detected.
- **Python version:** best-effort from `explicit_python_version`, `.python-version`, `pyproject.toml`, `setup.cfg`, `setup.py`, or `tox.ini`.
- **Overrides:** if provided, override install/gate commands and env, without changing the detection order.
- **Status:** `supported` when install is found; `partial` if install is found but a detected gate is missing; `unsupported` if no install is found. `missing` lists the missing pieces.
- **Explainability:** `decisions` include `value`, `reason`, `source`, and `defaulted_cmd` for install/gate/python decisions.
- **Detected tools:** `detected_tools` includes opt-in tools like `tox` and `make` (not auto-selected for gates).
- **Suggested profiles:** `suggested_profiles` provides opt-in profiles for detected tools (e.g., `tox`, `make`) without changing the primary profile.
- **Runtime vs hash:** `profile` is normalized (e.g., `working_dir` is `"."`), while `profile_runtime` carries absolute paths for debugging and does not affect `profile_id`.
- **Temp dir:** uses `PF_TMP_DIR` if set; otherwise `{repo_dir}/.tmp-test` is created once for skill-local scratch usage.

## CLI Example

```bash
python3 scripts/repo_profile_detect.py <<'JSON'
{
  "repo_dir": "/path/to/repo",
  "explicit_python_version": "3.11",
  "overrides": {
    "install_cmds": ["uv sync --all-extras --dev"],
    "test_cmd": "pytest",
    "lint_cmd": "ruff check .",
    "type_cmd": "mypy .",
    "env": {"ENV": "value"}
  }
}
JSON
```

## Resources

### scripts/
- `repo_profile_detect.py`: main detector (library + CLI entrypoint)
- `test_repo_profile_detect.py`: unit tests
