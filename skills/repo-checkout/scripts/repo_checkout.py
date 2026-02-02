from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import git
from git import BadName, GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

try:
    from pydantic import BaseModel, Field, ConfigDict
except Exception:  # pragma: no cover - pydantic v1
    from pydantic import BaseModel, Field
    ConfigDict = None


class RepoCheckoutRequest(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    repo_url: str = Field(..., description="Git repository URL or local path")
    commit_sha: str = Field(..., description="Commit SHA to checkout")
    workspace_root: str = Field(..., description="Root directory for all repo checkouts")
    repo_id: Optional[str] = Field(None, description="Optional stable repo directory name")
    shallow_clone: bool = Field(False, description="Use shallow clone if true")
    clean_worktree: bool = Field(False, description="Reset and clean before checkout if true")


class RepoCheckoutResponse(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    repo_dir: str
    checked_out_sha: str
    is_cached: bool
    manifest_path: str


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _safe_repo_id(raw: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")
    return safe or "repo"


def _default_repo_id(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    path = parsed.path if parsed.scheme else repo_url
    name = Path(path).name
    if name.endswith(".git"):
        name = name[:-4]
    base = _safe_repo_id(name.lower())
    digest = hashlib.sha1(repo_url.encode("utf-8")).hexdigest()[:10]
    return f"{base}-{digest}"


def _ensure_within_root(path: Path, root: Path) -> None:
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    if not path_resolved.is_relative_to(root_resolved):
        raise ValueError(f"Path '{path_resolved}' is outside workspace root '{root_resolved}'.")


def _workspace_root(path_str: str) -> Path:
    root = Path(path_str)
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise RuntimeError(f"Workspace root '{root}' is not a directory.")
    if not os.access(root, os.W_OK):
        raise RuntimeError(f"Workspace root '{root}' is not writable.")
    return root


def _open_repo(repo_dir: Path) -> Repo:
    try:
        repo = Repo(repo_dir)
    except (InvalidGitRepositoryError, NoSuchPathError) as exc:
        raise RuntimeError(f"'{repo_dir}' is not a valid git repository.") from exc
    if repo.bare:
        raise RuntimeError(f"'{repo_dir}' is a bare repository.")
    return repo


def _commit_exists(repo: Repo, sha: str) -> bool:
    try:
        repo.git.rev_parse("--verify", f"{sha}^{{commit}}")
        return True
    except (GitCommandError, BadName):
        return False


def _is_shallow(repo: Repo) -> bool:
    try:
        result = repo.git.rev_parse("--is-shallow-repository")
        return result.strip().lower() == "true"
    except GitCommandError:
        return False


def _fetch(repo: Repo, shallow_clone: bool) -> None:
    if shallow_clone:
        try:
            repo.git.fetch("--depth", "1", "--tags", "origin")
            return
        except GitCommandError:
            pass
    repo.git.fetch("--tags", "origin")


def _ensure_commit(repo: Repo, sha: str, shallow_clone: bool) -> None:
    if _commit_exists(repo, sha):
        return

    if shallow_clone:
        try:
            repo.git.fetch("--depth", "1", "origin", sha)
        except GitCommandError:
            pass
        if _commit_exists(repo, sha):
            return
        try:
            if _is_shallow(repo):
                repo.git.fetch("--unshallow", "origin")
            else:
                repo.git.fetch("origin")
        except GitCommandError:
            repo.git.fetch("--tags", "origin")
        if _commit_exists(repo, sha):
            return
    else:
        _fetch(repo, shallow_clone=False)
        if _commit_exists(repo, sha):
            return

    raise RuntimeError(f"Commit SHA '{sha}' not found after fetch.")


def _clean_worktree(repo: Repo) -> None:
    repo.git.reset("--hard")
    repo.git.clean("-fdx")


def _git_describe(repo: Repo) -> Optional[str]:
    try:
        return repo.git.describe("--always", "--dirty", "--tags")
    except GitCommandError:
        return None


def _provenance(repo_url: str, commit_sha: str, repo: Repo) -> dict[str, Any]:
    try:
        git_version = repo.git.version().strip()
    except GitCommandError:
        git_version = None
    return {
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": sys.version.split()[0],
        "gitpython_version": getattr(git, "__version__", None),
        "git_version": git_version,
    }


def checkout_repo(request: RepoCheckoutRequest) -> RepoCheckoutResponse:
    root = _workspace_root(request.workspace_root)

    repo_id = _safe_repo_id(request.repo_id) if request.repo_id else _default_repo_id(request.repo_url)
    repo_dir = root / repo_id
    _ensure_within_root(repo_dir, root)

    if repo_dir.exists() and not (repo_dir / ".git").exists():
        raise RuntimeError(f"'{repo_dir}' exists but is not a git repository.")

    is_cached = (repo_dir / ".git").exists()

    if not is_cached:
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        if request.shallow_clone:
            repo = Repo.clone_from(request.repo_url, repo_dir, depth=1)
        else:
            repo = Repo.clone_from(request.repo_url, repo_dir)
    else:
        repo = _open_repo(repo_dir)

    if request.clean_worktree:
        _clean_worktree(repo)

    if is_cached:
        _fetch(repo, shallow_clone=request.shallow_clone)
    _ensure_commit(repo, request.commit_sha, request.shallow_clone)

    repo.git.checkout(request.commit_sha, force=True)

    checked_out_sha = repo.head.commit.hexsha
    if checked_out_sha != request.commit_sha:
        raise RuntimeError(
            f"Checked out SHA '{checked_out_sha}' does not match requested '{request.commit_sha}'."
        )

    manifest_dir = repo_dir / ".pf_manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    provenance_manifest = manifest_dir / "manifest.json"
    provenance_payload = _provenance(request.repo_url, request.commit_sha, repo)
    provenance_manifest.write_text(json.dumps(provenance_payload, indent=2), encoding="utf-8")

    response = RepoCheckoutResponse(
        repo_dir=str(repo_dir),
        checked_out_sha=checked_out_sha,
        is_cached=is_cached,
        manifest_path=str(provenance_manifest),
    )

    checkout_manifest = {
        "request": _model_dump(request),
        "response": _model_dump(response),
        "git_describe": _git_describe(repo),
        "provenance_manifest": str(provenance_manifest),
    }
    (manifest_dir / "repo_checkout.json").write_text(
        json.dumps(checkout_manifest, indent=2), encoding="utf-8"
    )

    return response


if __name__ == "__main__":
    raw = json.loads(sys.stdin.read())
    req = RepoCheckoutRequest(**raw)
    resp = checkout_repo(req)
    print(json.dumps(_model_dump(resp), indent=2))
