---
name: mutation-apply
description: Apply deterministic code mutations to induce failures, produce unified diffs and metadata, and enforce strict change limits. Use when Codex needs to select a target file, apply a seeded mutation operator, and save mutation artifacts under .pf_manifest.
---

# Mutation Apply

## Overview

Apply a small deterministic mutation to a repo file, then save a unified diff and metadata to `.pf_manifest/mutations/`.

## Workflow

1. Provide `repo_dir` and `seed`; optionally set `operator_id` and `target_file`.
2. Run `scripts/mutation_apply.py` (CLI reads JSON from stdin, writes JSON response to stdout).
3. Use the mutation diff + metadata for downstream mutation testing.

## Deterministic Rules

- If `target_file` is omitted, select from Python files in the repo (excluding tests by default).
- Selection is seeded and deterministic.
- AST-aware mutation uses `tree-sitter-languages` when available; falls back to safe text mutation.
- Limits: `max_files_changed=1` (enforced), `max_lines_changed<=30` by default.
- Uses `PF_TMP_DIR` if set; otherwise `{repo_dir}/.tmp-test` is created.
- Excludes infra by default: `skills/**`, `.system/**`, `.pf_manifest/**`, `.git/**`, `.venv/**`, `.tmp-test/**`, `__pycache__/**` (override via `exclude_paths` or explicit `target_file`).
- Hard deny: `.git/**` and `.pf_manifest/**` are never mutated, even with explicit `target_file`.
- Response includes selection transparency (`candidate_files_considered`, `excluded_by_pattern`, `excluded_tests_count`, `final_candidate_count`, `final_candidate_sample`).

## Operators

- `flip_comparison`: flip `==`/`!=`, `<`/`>`, `<=`/`>=` (AST-aware when possible).
- `negate_boolean`: flip `True`/`False`.

## CLI Example

```bash
python3 scripts/mutation_apply.py <<'JSON'
{
  "repo_dir": "/path/to/repo",
  "seed": 7,
  "operator_id": "flip_comparison",
  "target_file": "src/mod.py",
  "include_tests": false,
  "exclude_paths": ["skills/**"],
  "limits": {"max_files_changed": 1, "max_lines_changed": 30}
}
JSON
```

## Artifacts

- Diff: `{repo_dir}/.pf_manifest/mutations/{mutation_id}.diff`
- Metadata: `{repo_dir}/.pf_manifest/mutations/{mutation_id}.json`

## Resources

### scripts/
- `mutation_apply.py`: main mutator (library + CLI entrypoint)
- `test_mutation_apply.py`: unit tests
