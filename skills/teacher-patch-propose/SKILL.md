---
name: teacher-patch-propose
description: Propose a candidate patch from a context bundle by calling a teacher model through an abstract interface, then validate that the output is a unified diff and respects change constraints. Use when Codex needs to generate teacher diffs without applying them.
---

# Teacher Patch Propose

## Overview

Generate a candidate unified diff from a context bundle by invoking a teacher model via a provider interface, then validate that the diff is well-formed and respects strict constraints.

## Workflow

1. Provide a `context_bundle_path`, `constraints`, `model_id`, and `attempt`.
2. Set `TEACHER_PROVIDER` to a provider module (e.g., `module:generate`).
3. Run `scripts/teacher_patch_propose.py` (CLI reads JSON from stdin, writes JSON response to stdout).
4. Use the response `patch_text_path` and `is_valid_diff` for downstream evaluation.

## Request / Response Schema

**Request**

- `context_bundle_path` (string)
- `constraints` (object)
  - `max_files_changed` (int)
  - `max_lines_changed` (int)
  - `allow_tests_edit` (bool)
  - `allow_deps_edit` (bool)
- `model_id` (string)
- `attempt` (int)

**Response**

- `patch_text_path` (string)
- `is_valid_diff` (bool)
- `validation_errors` (list of strings)
- `attempt` (int)

## Deterministic Rules

- Teacher output must be unified diff only (no prose/markdown).
- Strict validation rejects non-diff output, parse errors, or constraint violations.
- Output is normalized before validation (strip markdown fences, drop leading narration, normalize line endings, ensure trailing newline).
- Constraints enforced outside the model:
  - `max_files_changed`
  - `max_lines_changed`
  - `allow_tests_edit` (default false)
  - `allow_deps_edit` (default false)
- Output artifacts are stored under `{repo_dir}/.pf_manifest/teacher/{run_id}/`.

## Failure Modes

- `not_unified_diff`: output is not a unified diff
- `diff_parse_error:*`: unidiff parsing failed
- `max_files_changed_exceeded`: too many files changed
- `max_lines_changed_exceeded`: too many lines changed
- `tests_edit_not_allowed`: tests edited when disallowed
- `deps_edit_not_allowed`: dependency files edited when disallowed
- Provider error: missing `TEACHER_PROVIDER`, import failure, or provider exception

## Determinism Notes

- Model output may be non-deterministic; this skill records `model_id`, `attempt`, and constraints in the run id.
- Provider selection is external via `TEACHER_PROVIDER`; ensure provider-side settings (temperature, seed) are fixed if determinism is required.
- Provider metadata can be recorded via `TEACHER_PROVIDER_META` (JSON), stored alongside the run.

## Provider Interface

Set `TEACHER_PROVIDER` to a module path, optionally with `:callable`.

- Example: `TEACHER_PROVIDER=my_provider:generate`
- Callable signature: `generate(prompt: str, model_id: str, attempt: int, context: dict) -> str`

The provider is required; no built-in provider is included.

## Codex CLI Provider

Use the Codex CLI as a provider in non-interactive, read-only mode.

Environment:

```
export TEACHER_PROVIDER=teacher_providers.codex_cli_provider:generate
export TEACHER_MODEL_ID=gpt-5.2-thinking
export TEACHER_PROVIDER_META='{\"repo_dir\":\"/path/to/repo\",\"timeout_s\":180}'
```

Notes:

- `repo_dir` is required in `TEACHER_PROVIDER_META`.
- Optional meta fields:
  - `timeout_s` (default 180)
  - `use_json` (default false)
  - `json_flag` (default `--json`)
  - `model_flag` (default `--model`)
  - `use_model_flag` (default true)

## Smoke Check (Codex CLI Provider)

```bash
export TEACHER_PROVIDER=teacher_providers.codex_cli_provider:generate
export TEACHER_MODEL_ID=gpt-5.2-thinking
export TEACHER_PROVIDER_META='{\"repo_dir\":\"/path/to/repo\",\"timeout_s\":180}'

python3 scripts/teacher_patch_propose.py <<'JSON'
{
  \"context_bundle_path\": \"/path/to/repo/.pf_manifest/context/<id>.json\",
  \"constraints\": {
    \"max_files_changed\": 2,
    \"max_lines_changed\": 60,
    \"allow_tests_edit\": false,
    \"allow_deps_edit\": false
  },
  \"model_id\": \"gpt-5.2-thinking\",
  \"attempt\": 1
}
JSON
```

## Artifacts

- `raw.txt`: raw model output
- `patch.diff`: normalized diff if valid, empty if invalid
- `meta.json`: provider ref, provider metadata, prompt hash, and normalization notes

## CLI Example

```bash
python3 scripts/teacher_patch_propose.py <<'JSON'
{
  "context_bundle_path": "/path/to/repo/.pf_manifest/context/<id>.json",
  "constraints": {
    "max_files_changed": 1,
    "max_lines_changed": 30,
    "allow_tests_edit": false,
    "allow_deps_edit": false
  },
  "model_id": "teacher-model",
  "attempt": 1
}
JSON
```

## Output

- `patch_text_path`: `{repo_dir}/.pf_manifest/teacher/{run_id}/patch.diff`
- `is_valid_diff`: whether the diff passed validation
- `validation_errors`: list of validation error codes
- `attempt`: attempt number

## Resources

### scripts/
- `teacher_patch_propose.py`: main entrypoint (provider call + validation)
- `test_teacher_patch_propose.py`: unit tests for validation behavior
