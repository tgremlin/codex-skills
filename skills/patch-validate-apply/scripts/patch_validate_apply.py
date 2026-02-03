from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

try:
    from pydantic import BaseModel, Field, ConfigDict
except Exception:  # pragma: no cover
    from pydantic import BaseModel, Field
    ConfigDict = None

try:
    from unidiff import PatchSet
except Exception:  # pragma: no cover
    PatchSet = None

DEFAULT_MAX_FILES_CHANGED = 1
DEFAULT_MAX_LINES_CHANGED = 30


class PatchPolicy(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover
        class Config:
            extra = "forbid"

    max_files_changed: int = DEFAULT_MAX_FILES_CHANGED
    max_lines_changed: int = DEFAULT_MAX_LINES_CHANGED
    allow_tests_edit: bool = False
    allow_deps_edit: bool = False


class PatchApplyRequest(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover
        class Config:
            extra = "forbid"

    repo_dir: str = Field(..., description="Path to repo root")
    patch_text_path: str = Field(..., description="Path to unified diff text")
    policy: PatchPolicy
    fail_on_suspicious: bool = True


class PatchApplyResponse(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover
        class Config:
            extra = "forbid"

    applied: bool
    files_changed: int
    lines_changed: int
    suspicious_findings: list[str]
    apply_report_path: str


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def _read_patch_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def _normalize_patch_text(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized != text:
        notes.append("line_endings_normalized")
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
        notes.append("trailing_newline_added")
    return normalized, notes


def _is_unified_diff(text: str) -> bool:
    if not text.strip():
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    first = lines[0]
    return first.startswith("--- ") or first.startswith("diff --git ")


def _parse_diff(text: str) -> PatchSet:
    if PatchSet is None:
        raise RuntimeError("unidiff is required for diff parsing")
    return PatchSet(text)


def _normalize_path(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _diff_paths(patch: PatchSet) -> list[str]:
    paths = []
    for f in patch:
        paths.append(_normalize_path(f.path))
    return paths


def _count_changed_lines(patch: PatchSet) -> int:
    count = 0
    for f in patch:
        for hunk in f:
            for line in hunk:
                if line.is_added or line.is_removed:
                    count += 1
    return count


def _is_test_path(path: str) -> bool:
    parts = [part.lower() for part in Path(path).parts]
    if "tests" in parts or "test" in parts:
        return True
    name = Path(path).name.lower()
    return name.startswith("test_") or name.endswith("_test.py")


def _is_deps_path(path: str) -> bool:
    name = Path(path).name.lower()
    if name in {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "pipfile",
        "pipfile.lock",
        "poetry.lock",
        "uv.lock",
        "requirements.in",
        "requirements-dev.txt",
        "requirements.txt",
    }:
        return True
    if name.startswith("requirements") and name.endswith(".txt"):
        return True
    return False


def _is_absolute_deny(path: str) -> bool:
    parts = [part.lower() for part in Path(path).parts]
    return any(part in {".git", ".pf_manifest"} for part in parts)


def _find_suspicious(patch: PatchSet) -> list[str]:
    findings: list[str] = []
    skip_patterns = [
        r"pytest\.skip",
        r"pytest\.xfail",
        r"pytest\.mark\.skip",
        r"pytest\.mark\.xfail",
        r"unittest\.skip",
        r"skipTest",
        r"@unittest\.skip",
        r"@pytest\.mark\.skip",
        r"@pytest\.mark\.xfail",
        r"\bxfail\b",
    ]
    skip_re = re.compile("|".join(skip_patterns))
    except_re = re.compile(r"^\s*except\s+(Exception|BaseException)\b|^\s*except\s*:\s*$")
    comment_re = re.compile(r"^\s*(#|//|/\*)")

    for f in patch:
        for hunk in f:
            lines = list(hunk)
            for idx, line in enumerate(lines):
                if not line.is_added:
                    continue
                text = line.value
                if skip_re.search(text):
                    findings.append("skip_tests")
                if except_re.search(text):
                    findings.append("blanket_exception")
                if comment_re.search(text):
                    # flag if adjacent removed line was not comment
                    removed_prev = idx > 0 and lines[idx - 1].is_removed and not comment_re.search(lines[idx - 1].value)
                    removed_next = idx + 1 < len(lines) and lines[idx + 1].is_removed and not comment_re.search(lines[idx + 1].value)
                    if removed_prev or removed_next:
                        findings.append("commented_out_code")
    # de-dupe deterministically
    return sorted(set(findings))


def _apply_check(repo_dir: Path, patch_path: Path) -> tuple[bool, str, int]:
    cmd = ["git", "apply", "--check", "--recount", str(patch_path)]
    result = subprocess.run(cmd, cwd=str(repo_dir), capture_output=True, text=True)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output, result.returncode


def _apply_patch(repo_dir: Path, patch_path: Path) -> tuple[bool, str, int]:
    cmd = ["git", "apply", str(patch_path)]
    result = subprocess.run(cmd, cwd=str(repo_dir), capture_output=True, text=True)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output, result.returncode


def _git_status(repo_dir: Path, cap: int = 50) -> dict[str, Any]:
    cmd = ["git", "status", "--porcelain"]
    result = subprocess.run(cmd, cwd=str(repo_dir), capture_output=True, text=True)
    lines = []
    if result.stdout:
        lines = [line for line in result.stdout.splitlines() if line.strip()]
    count = len(lines)
    truncated = count > cap
    entries = lines[:cap]
    return {"count": count, "entries": entries, "truncated": truncated}


def validate_and_apply(request: PatchApplyRequest) -> PatchApplyResponse:
    repo_dir = Path(request.repo_dir).resolve()
    patch_path = Path(request.patch_text_path).resolve()
    if not repo_dir.exists():
        raise FileNotFoundError(f"Repo dir '{repo_dir}' does not exist")
    if not patch_path.exists():
        raise FileNotFoundError(f"Patch file '{patch_path}' does not exist")

    patch_text = _read_patch_text(patch_path)
    patch_text, normalization_notes = _normalize_patch_text(patch_text)
    validation_errors: list[str] = []
    suspicious_findings: list[str] = []

    files_changed = 0
    lines_changed = 0
    applied = False
    apply_output = ""
    apply_exit: Optional[int] = None
    apply_check_output = ""
    apply_check_exit: Optional[int] = None
    patch_paths: list[str] = []

    git_status_before = _git_status(repo_dir)

    patch_sha256 = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
    apply_payload = {
        "repo_dir": str(repo_dir),
        "patch_sha256": patch_sha256,
        "policy": _model_dump(request.policy),
        "fail_on_suspicious": request.fail_on_suspicious,
    }
    apply_id = hashlib.sha256(_stable_json(apply_payload).encode("utf-8")).hexdigest()[:16]
    report_dir = repo_dir / ".pf_manifest" / "apply"
    report_path = report_dir / f"{apply_id}.json"
    normalized_patch_path = report_dir / f"{apply_id}.diff"
    _atomic_write_text(normalized_patch_path, patch_text)

    if not _is_unified_diff(patch_text):
        validation_errors.append("not_unified_diff")
    else:
        patch = _parse_diff(patch_text)
        patch_paths = _diff_paths(patch)
        files_changed = len(patch_paths)
        lines_changed = _count_changed_lines(patch)

        if files_changed > request.policy.max_files_changed:
            validation_errors.append("max_files_changed_exceeded")
        if lines_changed > request.policy.max_lines_changed:
            validation_errors.append("max_lines_changed_exceeded")

        if any(_is_absolute_deny(p) for p in patch_paths):
            validation_errors.append("absolute_path_denied")
        if not request.policy.allow_tests_edit and any(_is_test_path(p) for p in patch_paths):
            validation_errors.append("tests_edit_not_allowed")
        if not request.policy.allow_deps_edit and any(_is_deps_path(p) for p in patch_paths):
            validation_errors.append("deps_edit_not_allowed")

        suspicious_findings = _find_suspicious(patch)
        if suspicious_findings and request.fail_on_suspicious:
            validation_errors.append("suspicious_patch")

        if not validation_errors:
            ok, output, exit_code = _apply_check(repo_dir, normalized_patch_path)
            apply_check_output = output
            apply_check_exit = exit_code
            if not ok:
                validation_errors.append("patch_apply_check_failed")

    if not validation_errors:
        ok, output, exit_code = _apply_patch(repo_dir, normalized_patch_path)
        applied = ok
        apply_output = output
        apply_exit = exit_code
        if not ok:
            validation_errors.append("patch_apply_failed")

    git_status_after = _git_status(repo_dir)

    report = {
        "apply_id": apply_id,
        "repo_dir": str(repo_dir),
        "patch_text_path": str(patch_path),
        "normalized_patch_path": str(normalized_patch_path),
        "patch_sha256": patch_sha256,
        "normalization_notes": normalization_notes,
        "deny_rules": {
            "deny_tests_edit": not request.policy.allow_tests_edit,
            "deny_deps_edit": not request.policy.allow_deps_edit,
            "deny_paths": [".git", ".pf_manifest"],
        },
        "policy": _model_dump(request.policy),
        "fail_on_suspicious": request.fail_on_suspicious,
        "files_changed": files_changed,
        "lines_changed": lines_changed,
        "applied_files": patch_paths if applied else [],
        "suspicious_findings": suspicious_findings,
        "validation_errors": validation_errors,
        "git_status_before": git_status_before,
        "git_status_after": git_status_after,
        "apply_check": {
            "exit_code": apply_check_exit,
            "output": apply_check_output,
        },
        "apply": {
            "applied": applied,
            "exit_code": apply_exit,
            "output": apply_output,
        },
    }
    _atomic_write_text(report_path, json.dumps(report, indent=2, sort_keys=True))

    return PatchApplyResponse(
        applied=applied,
        files_changed=files_changed,
        lines_changed=lines_changed,
        suspicious_findings=suspicious_findings,
        apply_report_path=str(report_path),
    )


def main() -> None:
    payload = sys.stdin.read().strip()
    if not payload:
        raise SystemExit("Expected JSON request on stdin")
    request = PatchApplyRequest(**json.loads(payload))
    response = validate_and_apply(request)
    sys.stdout.write(json.dumps(_model_dump(response), indent=2))


if __name__ == "__main__":
    main()
