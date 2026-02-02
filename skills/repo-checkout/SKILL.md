---
name: repo-checkout
description: Deterministic Git repository checkout to an exact commit SHA with caching and provenance manifests. Use when implementing or running a Python workflow that clones/fetches a repo to a specific SHA (detached HEAD), supports shallow clone with fallback, writes manifest files, and exposes Pydantic request/response models.
---

# Repo Checkout

## Overview
Build or run the deterministic repo checkout utility in `scripts/repo_checkout.py` using GitPython and Pydantic models, with caching, shallow-clone fallback, and manifest generation.

## Workflow
1. Use `RepoCheckoutRequest` to validate inputs (repo_url, commit_sha, workspace_root, repo_id, shallow_clone, clean_worktree).
2. Resolve a stable repo directory under `workspace_root` (use repo_id if provided, otherwise a deterministic slug+hash from repo_url).
3. If repo exists, fetch and verify the SHA; if not, clone (shallow optional).
4. If `clean_worktree` is true, `reset --hard` and `clean -fdx` before checkout.
5. Checkout the exact SHA (detached HEAD) and verify it matches.
6. Write provenance to `.pf_manifest/manifest.json` and write the full request/response record to `.pf_manifest/repo_checkout.json`.

## Scripts
- `scripts/repo_checkout.py`
  - Library entrypoint: `checkout_repo(request: RepoCheckoutRequest) -> RepoCheckoutResponse`
  - CLI mode: read JSON request from stdin and emit JSON response on stdout.
- `scripts/test_repo_checkout.py`
  - Unit test for local repo with two commits, verifies checkout and caching.

## Notes
- All paths must remain under the provided `workspace_root`.
- If `shallow_clone=True` and the SHA is missing, fall back to unshallow/full fetch.
- Fail fast if the repo exists but is not a valid git repository or if the workspace is not writable.
- Requires GitPython and Pydantic on Python 3.11+.
