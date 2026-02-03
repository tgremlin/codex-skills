from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from pydantic import BaseModel, Field, ConfigDict
except Exception:  # pragma: no cover - pydantic v1
    from pydantic import BaseModel, Field
    ConfigDict = None

try:
    from unidiff import PatchSet
except Exception:  # pragma: no cover
    PatchSet = None

DEFAULT_MAX_FILES_CHANGED = 1
DEFAULT_MAX_LINES_CHANGED = 30


class PatchConstraints(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    max_files_changed: int = DEFAULT_MAX_FILES_CHANGED
    max_lines_changed: int = DEFAULT_MAX_LINES_CHANGED
    allow_tests_edit: bool = False
    allow_deps_edit: bool = False


class TeacherPatchRequest(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    context_bundle_path: str = Field(..., description="Path to context bundle JSON")
    constraints: PatchConstraints
    model_id: str = Field(..., description="Teacher model identifier")
    attempt: int = Field(..., description="Attempt index")


class TeacherPatchResponse(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    patch_text_path: str
    is_valid_diff: bool
    validation_errors: list[str]
    attempt: int


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def _load_provider() -> Callable[[str, str, int, dict[str, Any]], str]:
    ref = os.environ.get("TEACHER_PROVIDER")
    if not ref:
        raise RuntimeError("TEACHER_PROVIDER is not set; cannot load teacher provider.")
    if ":" in ref:
        module_name, attr = ref.split(":", 1)
    else:
        module_name, attr = ref, "generate"
    module = importlib.import_module(module_name)
    provider = getattr(module, attr, None)
    if not callable(provider):
        raise RuntimeError("Teacher provider is not callable.")
    return provider


def _is_unified_diff(text: str) -> bool:
    if not text.strip():
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    first = lines[0]
    if not (first.startswith("--- ") or first.startswith("diff --git ")):
        return False
    return True


def _parse_diff(text: str) -> Optional[PatchSet]:
    if PatchSet is None:
        raise RuntimeError("unidiff is required for diff validation")
    return PatchSet(text)


def _is_hunk_error(error: Exception) -> bool:
    message = str(error)
    tokens = [
        "Unexpected hunk",
        "Hunk is longer than expected",
        "Hunk is shorter than expected",
    ]
    return any(token in message for token in tokens)


def _can_recount_hunks(lines: list[str]) -> bool:
    has_header = any(line.startswith("diff --git ") or line.startswith("--- ") for line in lines)
    if not has_header:
        return False
    in_hunk = False
    for line in lines:
        if line.startswith("@@ "):
            in_hunk = True
            continue
        if line.startswith("diff --git ") or line.startswith("--- ") or line.startswith("+++ "):
            in_hunk = False
            continue
        if not in_hunk:
            continue
        if not line:
            return False
        prefix = line[0]
        if prefix not in {" ", "+", "-", "\\"}:
            return False
    return True


def _recount_hunks(text: str) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines()
    if not _can_recount_hunks(lines):
        return text, {"hunks_total": 0, "hunks_recounted": 0, "recount_skipped": True}
    out: list[str] = []
    hunks_recounted = 0
    hunks_total = 0

    def is_header(line: str) -> bool:
        return line.startswith("diff --git ") or line.startswith("--- ") or line.startswith("+++ ")

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("@@ "):
            match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$", line)
            if not match:
                out.append(line)
                i += 1
                continue
            old_start = int(match.group(1))
            new_start = int(match.group(3))
            trailing = match.group(5) or ""
            hunk_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("@@ ") and not is_header(lines[i]):
                hunk_lines.append(lines[i])
                i += 1
            old_count = 0
            new_count = 0
            for hunk_line in hunk_lines:
                if not hunk_line:
                    continue
                prefix = hunk_line[0]
                if prefix == "+":
                    new_count += 1
                elif prefix == "-":
                    old_count += 1
                elif prefix == " ":
                    old_count += 1
                    new_count += 1
                elif prefix == "\\":
                    continue
                else:
                    # Unknown line prefix; keep counts unchanged.
                    continue
            hunks_total += 1
            new_header = f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{trailing}"
            if new_header != line:
                hunks_recounted += 1
            out.append(new_header)
            out.extend(hunk_lines)
            continue
        out.append(line)
        i += 1

    normalized = "\n".join(out)
    if text.endswith("\n"):
        normalized += "\n"
    return normalized, {"hunks_total": hunks_total, "hunks_recounted": hunks_recounted, "recount_skipped": False}

def _strict_hunk_headers(text: str) -> bool:
    pattern = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")
    for line in text.splitlines():
        if line.startswith("@@"):
            if not pattern.match(line):
                return False
    return True


def _apply_check(repo_dir: Path, patch_path: Path) -> tuple[bool, str, int]:
    cmd = ["git", "apply", "--check", "--recount", str(patch_path)]
    result = subprocess.run(
        cmd,
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output, result.returncode


def _strip_markdown_fences(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    if "```" not in text:
        return text, notes
    lines = text.splitlines()
    fenced = []
    in_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            if not in_fence:
                notes.append("markdown_fence_removed")
                in_fence = True
                continue
            in_fence = False
            continue
        if in_fence:
            fenced.append(line)
    if fenced:
        return "\n".join(fenced), notes
    return text, notes


def _strip_leading_narration(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.startswith("diff --git ") or line.startswith("--- "):
            if idx > 0:
                notes.append("leading_narration_removed")
            return "\n".join(lines[idx:]), notes
    return text, notes


def _normalize_diff_output(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized != text:
        notes.append("line_endings_normalized")
    normalized, fence_notes = _strip_markdown_fences(normalized)
    notes.extend(fence_notes)
    normalized, lead_notes = _strip_leading_narration(normalized)
    notes.extend(lead_notes)
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
        notes.append("trailing_newline_added")
    return normalized, notes


def _diff_paths(patch: PatchSet) -> list[str]:
    paths = []
    for f in patch:
        path = f.path
        if path.startswith("a/") or path.startswith("b/"):
            path = path[2:]
        paths.append(path)
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


def _validate_constraints(
    patch: PatchSet, constraints: PatchConstraints
) -> list[str]:
    errors: list[str] = []
    paths = _diff_paths(patch)
    if len(paths) > constraints.max_files_changed:
        errors.append("max_files_changed_exceeded")
    changed_lines = _count_changed_lines(patch)
    if changed_lines > constraints.max_lines_changed:
        errors.append("max_lines_changed_exceeded")
    if not constraints.allow_tests_edit:
        if any(_is_test_path(p) for p in paths):
            errors.append("tests_edit_not_allowed")
    if not constraints.allow_deps_edit:
        if any(_is_deps_path(p) for p in paths):
            errors.append("deps_edit_not_allowed")
    return errors


def _build_prompt(context_bundle: dict[str, Any], constraints: PatchConstraints) -> str:
    files = context_bundle.get("files", [])
    parts = []
    parts.append("You are a teacher model. Produce a unified diff only.")
    parts.append("No explanations or markdown.")
    parts.append("Minimal changes only. No refactors.")
    parts.append("Do not edit tests or dependencies unless allowed.")
    parts.append("")
    parts.append("Constraints:")
    parts.append(f"- max_files_changed: {constraints.max_files_changed}")
    parts.append(f"- max_lines_changed: {constraints.max_lines_changed}")
    parts.append(f"- allow_tests_edit: {constraints.allow_tests_edit}")
    parts.append(f"- allow_deps_edit: {constraints.allow_deps_edit}")
    parts.append("")
    parts.append("Context:")
    for entry in files:
        parts.append(f"FILE: {entry.get('path')}")
        for snippet in entry.get("snippets", []):
            parts.append(
                f"SNIPPET {snippet.get('kind')} {snippet.get('start_line')}-{snippet.get('end_line')}"
            )
            parts.append(snippet.get("text", ""))
        for hunk in entry.get("diff_hunks", []):
            parts.append("MUTATION_HUNK:")
            parts.append(hunk.get("hunk", ""))
        parts.append("")
    parts.append("Output unified diff only.")
    return "\n".join(parts)


def _invoke_provider(
    provider: Callable[..., Any],
    prompt: str,
    model_id: str,
    attempt: int,
    context_bundle: dict[str, Any],
    provider_meta: dict[str, Any],
) -> tuple[str, Optional[dict[str, Any]]]:
    try:
        sig = inspect.signature(provider)
        params = len(sig.parameters)
    except Exception:
        params = 0
    try:
        if params == 4:
            result = provider(prompt, model_id, attempt, context_bundle)
        elif params == 3:
            result = provider(prompt, model_id, provider_meta)
        elif params == 2:
            result = provider(prompt, model_id)
        elif params == 1:
            result = provider(prompt)
        else:
            result = provider(prompt, model_id, provider_meta)
    except TypeError:
        result = provider(prompt, model_id, provider_meta)

    provider_meta_out: Optional[dict[str, Any]] = None
    output: Any = result
    if isinstance(result, tuple) and len(result) == 2:
        output, provider_meta_out = result
    elif isinstance(result, dict) and "text" in result:
        output = result.get("text")
        provider_meta_out = result.get("meta")

    if not isinstance(output, str):
        output = str(output)
    return output, provider_meta_out


def propose_patch(request: TeacherPatchRequest) -> TeacherPatchResponse:
    context_path = Path(request.context_bundle_path)
    if not context_path.exists():
        raise FileNotFoundError(f"Context bundle '{context_path}' does not exist.")
    context_bundle = _read_json(context_path)
    repo_dir = Path(context_bundle.get("repo_dir", ".")).resolve()
    if not repo_dir.exists():
        raise FileNotFoundError("Repo dir from context bundle does not exist.")

    provider = _load_provider()

    prompt = _build_prompt(context_bundle, request.constraints)
    provider_meta = {
        "repo_dir": str(repo_dir),
        "context_id": context_bundle.get("context_id"),
        "attempt": request.attempt,
        "model_id": request.model_id,
        "constraints": _model_dump(request.constraints),
    }
    output, provider_meta_out = _invoke_provider(
        provider, prompt, request.model_id, request.attempt, context_bundle, provider_meta
    )
    normalized_output, normalization_notes = _normalize_diff_output(output)

    run_payload = {
        "context_id": context_bundle.get("context_id"),
        "model_id": request.model_id,
        "attempt": request.attempt,
        "constraints": _model_dump(request.constraints),
    }
    run_id = hashlib.sha256(_stable_json(run_payload).encode("utf-8")).hexdigest()[:16]
    out_dir = repo_dir / ".pf_manifest" / "teacher" / run_id
    raw_path = out_dir / "raw.txt"
    patch_path = out_dir / "patch.diff"
    normalized_path = out_dir / "patch.normalized.diff"
    apply_check_path = out_dir / "apply_check.log"
    meta_path = out_dir / "meta.json"

    _atomic_write_text(raw_path, output)

    validation_errors: list[str] = []
    is_valid_diff = False
    diff_rewritten = False
    diff_fix_summary: dict[str, Any] | None = None
    diff_valid_unidiff = False
    diff_header_strict = False
    diff_valid_applycheck = False
    diff_applycheck_error: str | None = None
    diff_applycheck_exit: int | None = None

    if not _is_unified_diff(normalized_output):
        validation_errors.append("not_unified_diff")
    else:
        try:
            patch = _parse_diff(normalized_output)
            diff_valid_unidiff = True
        except Exception as exc:
            if _is_hunk_error(exc):
                recount_output, recount_info = _recount_hunks(normalized_output)
                if recount_output != normalized_output:
                    diff_rewritten = True
                diff_fix_summary = recount_info
                if not recount_info.get("recount_skipped"):
                    normalization_notes.append("recount_hunks")
                    normalized_output = recount_output
                    try:
                        patch = _parse_diff(normalized_output)
                        diff_valid_unidiff = True
                    except Exception as recount_exc:
                        validation_errors.append(f"diff_parse_error:{recount_exc}")
                else:
                    validation_errors.append(f"diff_parse_error:{exc}")
            else:
                validation_errors.append(f"diff_parse_error:{exc}")

    if normalized_output:
        _atomic_write_text(normalized_path, normalized_output)

    if diff_valid_unidiff:
        if _strict_hunk_headers(normalized_output):
            diff_header_strict = True
        else:
            validation_errors.append("invalid_hunk_header")

    if diff_valid_unidiff and diff_header_strict:
        ok, apply_output, exit_code = _apply_check(repo_dir, normalized_path)
        diff_valid_applycheck = ok
        diff_applycheck_exit = exit_code
        _atomic_write_text(apply_check_path, apply_output)
        if not ok:
            diff_applycheck_error = apply_output.strip()[-2000:]
            validation_errors.append("diff_applycheck_failed")

    if diff_valid_unidiff and diff_header_strict and diff_valid_applycheck:
        constraint_errors = _validate_constraints(patch, request.constraints)
        if constraint_errors:
            validation_errors.extend(constraint_errors)
        else:
            is_valid_diff = True

    patch_content = normalized_output if is_valid_diff else ""
    _atomic_write_text(patch_path, patch_content)

    provider_meta_env = os.environ.get("TEACHER_PROVIDER_META")
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    meta_payload = {
        "context_id": context_bundle.get("context_id"),
        "model_id": request.model_id,
        "attempt": request.attempt,
        "constraints": _model_dump(request.constraints),
        "provider_ref": os.environ.get("TEACHER_PROVIDER"),
        "provider_meta": {
            "env": json.loads(provider_meta_env) if provider_meta_env else None,
            "provider": provider_meta_out,
        },
        "prompt_sha256": prompt_hash,
        "normalization_notes": normalization_notes,
        "diff_rewritten": diff_rewritten,
        "diff_fix_summary": diff_fix_summary,
        "diff_valid_unidiff": diff_valid_unidiff,
        "diff_header_strict": diff_header_strict,
        "diff_valid_applycheck": diff_valid_applycheck,
        "diff_applycheck_error": diff_applycheck_error,
        "diff_applycheck_exit": diff_applycheck_exit,
        "apply_check_log": str(apply_check_path),
    }
    _atomic_write_text(meta_path, json.dumps(meta_payload, indent=2, sort_keys=True))

    return TeacherPatchResponse(
        patch_text_path=str(patch_path),
        is_valid_diff=is_valid_diff,
        validation_errors=validation_errors,
        attempt=request.attempt,
    )


def main() -> None:
    payload = sys.stdin.read().strip()
    if not payload:
        raise SystemExit("Expected JSON request on stdin")
    request = TeacherPatchRequest(**json.loads(payload))
    response = propose_patch(request)
    sys.stdout.write(json.dumps(_model_dump(response), indent=2))


if __name__ == "__main__":
    main()
