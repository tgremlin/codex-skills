---
name: context-pack
description: Build deterministic, size-bounded context bundles from GateReports and mutation diffs for fixer/teacher models. Use when Codex must assemble minimal code context around failure signals and mutations without including full repositories.
---

# Context Pack

## Overview

Construct a deterministic, size-bounded context bundle by selecting only the files and snippets needed to fix a failure, based on GateReport signals and mutation diffs.

## Workflow

1. Provide `repo_dir`, `gate_report_path`, and optional `mutation_diff_path`.
2. Run `scripts/context_pack.py` (CLI reads JSON from stdin, writes JSON response to stdout).
3. Use the response `context_bundle_path` and `included_files` for downstream fixer/teacher models.

## Deterministic Rules

- Include files referenced by gate failure signals (paths/line numbers) and files touched in the mutation diff.
- Include snippet windows of `context_radius_lines` around error spans and mutation hunks.
- Optionally include surrounding function/class blocks using tree-sitter (Python) when available.
- Order files deterministically (mutation files first, then gate-signal files; ties by path).
- Enforce `max_files` and `max_bytes` with stable truncation (head/tail) when required.
- Exclude paths matching:
  - `.git/**`
  - `.pf_manifest/**`
  - `skills/**`
  - `.system/**`
- Graceful degradation:
  - If `python-unidiff` is unavailable, diff parsing is skipped and `diff_parse_status=unavailable`.
  - If `tree-sitter-languages` is unavailable, AST blocks are skipped and `ast_blocks_status=unavailable`.

## CLI Example

```bash
python3 scripts/context_pack.py <<'JSON'
{
  "repo_dir": "/path/to/repo",
  "gate_report_path": "/path/to/repo/.pf_manifest/gates/<run>.json",
  "mutation_diff_path": "/path/to/repo/.pf_manifest/mutations/<id>.diff",
  "max_bytes": 250000,
  "max_files": 5,
  "context_radius_lines": 20
}
JSON
```

## Output

- `context_bundle_path`: `{repo_dir}/.pf_manifest/context/{context_id}.json`
- `included_files`: list of relative file paths
- `total_bytes`: final bundle size
- `truncation_applied`: whether truncation occurred
- Bundle metadata includes `included_files_count`, `included_bytes`, `excluded_by_rule` (capped samples), and `selection_order`. If the bundle still exceeds `max_bytes`, optional metadata is dropped and `metadata_trimmed=true`. Diff parsing errors set `diff_parse_status=error` with `diff_parse_error` populated; when no diff is provided, `diff_parse_status=skipped`.

## Resources

### scripts/
- `context_pack.py`: main entrypoint (Pydantic request/response, deterministic selection, truncation)
- `test_context_pack.py`: unit tests for selection + truncation
