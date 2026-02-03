from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import os
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from pydantic import BaseModel, Field, ConfigDict
except Exception:  # pragma: no cover - pydantic v1
    from pydantic import BaseModel, Field
    ConfigDict = None

try:
    from tree_sitter_languages import get_parser
except Exception:  # pragma: no cover - optional
    get_parser = None


DEFAULT_MAX_FILES_CHANGED = 1
DEFAULT_MAX_LINES_CHANGED = 30
DEFAULT_TMP_DIR_NAME = ".tmp-test"


class MutationLimits(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    max_files_changed: int = DEFAULT_MAX_FILES_CHANGED
    max_lines_changed: int = DEFAULT_MAX_LINES_CHANGED


class MutationApplyRequest(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    repo_dir: str = Field(..., description="Repository directory")
    seed: int = Field(..., description="Seed for deterministic selection")
    operator_id: Optional[str] = Field(None, description="Optional operator id")
    target_file: Optional[str] = Field(None, description="Optional target file path")
    include_tests: bool = Field(False, description="Include tests when selecting target")
    exclude_paths: Optional[list[str]] = Field(
        None, description="Optional list of glob patterns to exclude"
    )
    limits: Optional[MutationLimits] = None


class MutationApplyResponse(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    mutation_id: str
    operator_id: str
    target_file: str
    diff_path: str
    changed_lines: int
    applied: bool
    reason: Optional[str] = None
    selection: Optional[dict[str, Any]] = None


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _tmp_root(repo_dir: Path) -> Path:
    env_dir = os.environ.get("PF_TMP_DIR")
    if env_dir:
        return Path(env_dir)
    return repo_dir / DEFAULT_TMP_DIR_NAME


def _default_exclude_patterns() -> list[str]:
    return [
        "skills/**",
        ".system/**",
        ".pf_manifest/**",
        ".git/**",
        ".venv/**",
        ".tmp-test/**",
        "__pycache__/**",
    ]


def _hard_deny_patterns() -> list[str]:
    return [
        ".git/**",
        ".pf_manifest/**",
    ]


def _matches_exclude(path: Path, patterns: list[str]) -> bool:
    rel = path.as_posix()
    for pattern in patterns:
        if fnmatch.fnmatch(rel, pattern):
            return True
    return False


def _is_test_path(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    if "tests" in parts or "test" in parts:
        return True
    name = path.name.lower()
    return name.startswith("test_") or name.endswith("_test.py")


def _list_python_files(
    repo_dir: Path,
    include_tests: bool,
    exclude_patterns: list[str],
    hard_deny_patterns: list[str],
) -> tuple[list[Path], dict[str, int], int, int]:
    files: list[Path] = []
    excluded_by_pattern: dict[str, int] = {}
    excluded_tests = 0
    considered = 0
    skip_dirs = {".git", ".venv", ".tmp-test", ".pf_manifest", "__pycache__"}
    for root, dirnames, filenames in os.walk(repo_dir):
        dirnames[:] = [name for name in dirnames if name not in skip_dirs]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = Path(root) / filename
            rel = path.relative_to(repo_dir)
            considered += 1
            if _matches_exclude(rel, hard_deny_patterns):
                excluded_by_pattern["<hard_deny>"] = excluded_by_pattern.get("<hard_deny>", 0) + 1
                continue
            if _matches_exclude(rel, exclude_patterns):
                for pattern in exclude_patterns:
                    if fnmatch.fnmatch(rel.as_posix(), pattern):
                        excluded_by_pattern[pattern] = excluded_by_pattern.get(pattern, 0) + 1
                        break
                continue
            if not include_tests and _is_test_path(path.relative_to(repo_dir)):
                excluded_tests += 1
                continue
            files.append(path)
    return sorted(files), excluded_by_pattern, excluded_tests, considered


def _unified_diff(original: str, mutated: str, rel_path: str) -> str:
    original_lines = original.splitlines(keepends=True)
    mutated_lines = mutated.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines,
        mutated_lines,
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        lineterm="",
    )
    return "\n".join(diff)


def _count_changed_lines(diff_text: str) -> int:
    count = 0
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+") or line.startswith("-"):
            count += 1
    return count


def _mutation_id(seed: int, operator_id: str, target_file: str) -> str:
    payload = f"{seed}:{operator_id}:{target_file}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


@dataclass
class MutationOperator:
    operator_id: str
    description: str
    mutate: Callable[[str, int], Optional[str]]


def _flip_operator(op: str) -> str:
    mapping = {
        "==": "!=",
        "!=": "==",
        "<": ">",
        ">": "<",
        "<=": ">=",
        ">=": "<=",
    }
    return mapping.get(op, op)


def _ast_flip_comparison(text: str, seed: int) -> Optional[str]:
    if get_parser is None:
        return None
    try:
        parser = get_parser("python")
    except Exception:
        return None
    tree = parser.parse(bytes(text, "utf-8"))
    candidates: list[tuple[int, int, str]] = []

    def walk(node):
        if node.type in {"==", "!=", "<", ">", "<=", ">="}:
            candidates.append((node.start_byte, node.end_byte, node.type))
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    if not candidates:
        return None

    rng = random.Random(seed)
    start, end, op = rng.choice(candidates)
    mutated = text[:start] + _flip_operator(op) + text[end:]
    return mutated


def _text_flip_comparison(text: str, seed: int) -> Optional[str]:
    ops = ["==", "!=", "<=", ">=", "<", ">"]
    matches: list[tuple[int, int, str]] = []
    for op in ops:
        for match in re.finditer(re.escape(op), text):
            matches.append((match.start(), match.end(), op))
    if not matches:
        return None
    rng = random.Random(seed)
    start, end, op = rng.choice(matches)
    mutated = text[:start] + _flip_operator(op) + text[end:]
    return mutated


def _flip_comparison(text: str, seed: int) -> Optional[str]:
    mutated = _ast_flip_comparison(text, seed)
    if mutated is not None:
        return mutated
    return _text_flip_comparison(text, seed)


def _negate_boolean(text: str, seed: int) -> Optional[str]:
    tokens = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"\bTrue\b|\bFalse\b", text)]
    if not tokens:
        return None
    rng = random.Random(seed)
    start, end, token = rng.choice(tokens)
    replacement = "False" if token == "True" else "True"
    return text[:start] + replacement + text[end:]


def _operators() -> dict[str, MutationOperator]:
    return {
        "flip_comparison": MutationOperator(
            operator_id="flip_comparison",
            description="Flip a comparison operator (AST-aware when possible).",
            mutate=_flip_comparison,
        ),
        "negate_boolean": MutationOperator(
            operator_id="negate_boolean",
            description="Flip a boolean literal.",
            mutate=_negate_boolean,
        ),
    }


def _choose_operator(seed: int, operator_id: Optional[str]) -> MutationOperator:
    ops = _operators()
    if operator_id:
        if operator_id not in ops:
            raise ValueError(f"Unknown operator_id '{operator_id}'.")
        return ops[operator_id]
    rng = random.Random(seed)
    return ops[sorted(ops.keys())[rng.randrange(len(ops))]]


def _select_target_file(
    repo_dir: Path,
    seed: int,
    target_file: Optional[str],
    include_tests: bool,
    exclude_patterns: list[str],
    hard_deny_patterns: list[str],
) -> tuple[Path, dict[str, Any]]:
    selection_report: dict[str, Any] = {}
    if target_file:
        path = Path(target_file)
        if not path.is_absolute():
            path = repo_dir / path
        if not path.exists():
            raise FileNotFoundError(f"Target file '{path}' not found.")
        rel = path.relative_to(repo_dir)
        if _matches_exclude(rel, hard_deny_patterns):
            raise ValueError(f"Target file '{rel}' is in a hard-deny path.")
        selection_report = {
            "candidate_files_considered": 1,
            "excluded_by_pattern": {},
            "excluded_tests_count": 0,
            "final_candidate_count": 1,
            "final_candidate_sample": [str(rel)],
        }
        return path, selection_report

    candidates, excluded_by_pattern, excluded_tests, considered = _list_python_files(
        repo_dir, include_tests, exclude_patterns, hard_deny_patterns
    )
    if not candidates:
        raise RuntimeError("No python source files found to mutate.")
    rng = random.Random(seed)
    selection_report = {
        "candidate_files_considered": considered,
        "excluded_by_pattern": excluded_by_pattern,
        "excluded_tests_count": excluded_tests,
        "final_candidate_count": len(candidates),
        "final_candidate_sample": [str(path.relative_to(repo_dir)) for path in candidates[:5]],
    }
    return candidates[rng.randrange(len(candidates))], selection_report


def apply_mutation(request: MutationApplyRequest) -> MutationApplyResponse:
    repo_dir = Path(request.repo_dir).resolve()
    if not repo_dir.exists():
        raise FileNotFoundError(f"Repo dir '{repo_dir}' does not exist.")
    if not repo_dir.is_dir():
        raise NotADirectoryError(f"Repo dir '{repo_dir}' is not a directory.")

    _tmp_root(repo_dir).mkdir(parents=True, exist_ok=True)

    limits = request.limits or MutationLimits()
    if limits.max_files_changed != DEFAULT_MAX_FILES_CHANGED:
        raise ValueError("Only max_files_changed=1 is supported.")

    operator = _choose_operator(request.seed, request.operator_id)
    exclude_patterns = request.exclude_paths or _default_exclude_patterns()
    hard_deny_patterns = _hard_deny_patterns()
    target_path, selection_report = _select_target_file(
        repo_dir,
        request.seed,
        request.target_file,
        request.include_tests,
        exclude_patterns,
        hard_deny_patterns,
    )

    original_text = target_path.read_text(encoding="utf-8")
    mutated_text = operator.mutate(original_text, request.seed)
    reason = None
    diff_text = ""
    changed_lines = 0
    applied = False
    if mutated_text is None or mutated_text == original_text:
        reason = "no_applicable_sites"
    else:
        diff_text = _unified_diff(original_text, mutated_text, str(target_path.relative_to(repo_dir)))
        changed_lines = _count_changed_lines(diff_text)
        if changed_lines > limits.max_lines_changed:
            reason = "limit_exceeded"
            applied = False
        else:
            target_path.write_text(mutated_text, encoding="utf-8")
            applied = True

    mutation_id = _mutation_id(request.seed, operator.operator_id, str(target_path.relative_to(repo_dir)))
    mutations_dir = repo_dir / ".pf_manifest" / "mutations"
    diff_path = mutations_dir / f"{mutation_id}.diff"
    if diff_text:
        _atomic_write_text(diff_path, diff_text + ("\n" if diff_text else ""))

    metadata = {
        "mutation_id": mutation_id,
        "operator_id": operator.operator_id,
        "seed": request.seed,
        "target_file": str(target_path.relative_to(repo_dir)),
        "changed_lines": changed_lines,
        "applied": applied,
        "reason": reason,
        "exclude_paths": exclude_patterns,
        "hard_deny_paths": hard_deny_patterns,
        "selection": selection_report,
        "limits": _model_dump(limits),
    }
    metadata_path = mutations_dir / f"{mutation_id}.json"
    _atomic_write_text(metadata_path, json.dumps(metadata, indent=2))

    return MutationApplyResponse(
        mutation_id=mutation_id,
        operator_id=operator.operator_id,
        target_file=str(target_path.relative_to(repo_dir)),
        diff_path=str(diff_path) if diff_text else "",
        changed_lines=changed_lines,
        applied=applied,
        reason=reason,
        selection=selection_report,
    )


if __name__ == "__main__":
    raw = json.loads(sys.stdin.read())
    req = MutationApplyRequest(**raw)
    resp = apply_mutation(req)
    print(json.dumps(_model_dump(resp), indent=2))
