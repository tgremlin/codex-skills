---
name: patch-validate-apply
description: Validate unified diffs against safety policy (deny tests/deps by default, absolute deny .git/.pf_manifest), detect suspicious bypass patterns, and apply patches deterministically. Use when Codex needs to accept/reject and apply a patch safely with an auditable report.
---

# Patch Validate Apply

## Overview

Validate a unified diff against policy rules, detect suspicious patch patterns, and apply the patch cleanly to a repo with a deterministic JSON report.

## Workflow

1. **Parse and validate diff**
   - Require unified diff headers.
   - Enforce caps: max files changed, max lines changed.
   - Default deny edits to `tests/**` and dependency files.
   - Absolute deny edits to `.git/**` and `.pf_manifest/**`.
   - Detect suspicious patterns (test skipping, blanket exceptions, commented-out code).
2. **Apply check + apply**
   - Use `git apply --check --recount` to verify the patch applies cleanly.
   - Apply with `git apply` if checks pass.
3. **Write report**
   - Write apply report JSON to `{repo_dir}/.pf_manifest/apply/{apply_id}.json`.
   - Report includes deny rules, git status before/after, and applied files list.

## Script

### scripts/patch_validate_apply.py

Reads a JSON request from stdin and prints a JSON response.

**Request schema**
- `repo_dir`
- `patch_text_path`
- `policy`:
  - `max_files_changed`
  - `max_lines_changed`
  - `allow_tests_edit` (default false)
  - `allow_deps_edit` (default false)
- `fail_on_suspicious` (bool)

**Response schema**
- `applied` (bool)
- `files_changed`
- `lines_changed`
- `suspicious_findings` (list)
- `apply_report_path`

**Example**
```bash
python scripts/patch_validate_apply.py <<'JSON'
{
  "repo_dir": "/path/to/repo",
  "patch_text_path": "/path/to/patch.diff",
  "policy": {
    "max_files_changed": 2,
    "max_lines_changed": 60,
    "allow_tests_edit": false,
    "allow_deps_edit": false
  },
  "fail_on_suspicious": true
}
JSON
```

## Notes

- Suspicious patterns are heuristic and conservative; use `fail_on_suspicious=false` to allow inspection-only runs.
- Application uses `git apply`, so patches must be applyable without fuzz.

## Resources

### scripts/
- `patch_validate_apply.py` — validator + applier
- `test_patch_validate_apply.py` — unit tests
