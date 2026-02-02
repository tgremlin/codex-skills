---
name: container-image-build
description: Build or reuse deterministic Docker images for a repo based on a repo_profile.json profile. Use when Codex needs to generate a Dockerfile, build an image tagged by profile_id, manage build logs/manifests, or reuse cached images for running install/gates.
---

# Container Image Build

## Overview

Generate a deterministic Dockerfile from a repo profile, build a Docker image tagged by `profile_id`, and store build artifacts under `.pf_manifest/image_build/`.

## Workflow

1. Provide `repo_dir` and `profile_path` from a repo profile detector.
2. Run `scripts/container_image_build.py` (CLI reads JSON from stdin, writes JSON response to stdout).
3. Use the response `image_tag` to run install/gates inside the container.

## Deterministic Rules

- Base image: `python:{version}-slim`, where `{version}` is derived from `profile.python_version_target` or defaults to `3.11`.
- System packages: `git build-essential curl` installed via `apt-get`.
- `install_cmds` are executed as `RUN` steps in order.
- Environment variables are written in sorted key order for stability.
- Dockerfile content is stable given the same profile.

## Caching Behavior

- Image tag: `patchfoundry/{profile_id}:latest`.
- If the image already exists locally and `force_rebuild` is false, the build is skipped and `reused_cache=true`.
- If `image_cache_dir` is provided, a cached tarball `{profile_id}.tar` is loaded/saved to improve reuse.
- If `image_cache_dir` is omitted, the builder uses `{repo_dir}/.tmp-test/image-cache` by default (override with `PF_TMP_DIR`).
- Docker layer cache is used by default via `cache_from`.

## CLI Example

```bash
python3 scripts/container_image_build.py <<'JSON'
{
  "repo_dir": "/path/to/repo",
  "profile_path": "/path/to/repo/.pf_manifest/repo_profile.json",
  "image_cache_dir": "/tmp/image-cache",
  "force_rebuild": false
}
JSON
```

## Resources

### scripts/
- `container_image_build.py`: main builder (library + CLI entrypoint)
- `test_container_image_build.py`: integration test (skips if Docker unavailable)
