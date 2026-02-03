from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

try:
    from pydantic import BaseModel, Field, ConfigDict
except Exception:  # pragma: no cover - pydantic v1
    from pydantic import BaseModel, Field
    ConfigDict = None

try:
    from unidiff import PatchSet
except Exception:  # pragma: no cover
    PatchSet = None

try:
    from tree_sitter_languages import get_parser
except Exception:  # pragma: no cover - optional
    get_parser = None

DEFAULT_MAX_BYTES = 250000
DEFAULT_MAX_FILES = 5
DEFAULT_RADIUS = 20
EXCLUDE_PATTERNS = [
    ".git/**",
    ".pf_manifest/**",
    "skills/**",
    ".system/**",
]
EXCLUDE_CAP = 10
_AST_AVAILABLE: Optional[bool] = None
_AST_PARSER = None


class ContextPackRequest(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    repo_dir: str = Field(..., description="Repository directory")
    gate_report_path: str = Field(..., description="Path to GateReport JSON")
    mutation_diff_path: Optional[str] = Field(
        None, description="Path to mutation diff (optional)"
    )
    max_bytes: int = Field(DEFAULT_MAX_BYTES, description="Max bundle size in bytes")
    max_files: int = Field(DEFAULT_MAX_FILES, description="Max files to include")
    context_radius_lines: int = Field(DEFAULT_RADIUS, description="Lines around error spans")
    include_ast_blocks: bool = Field(True, description="Include AST block context when available")


class ContextPackResponse(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    context_bundle_path: str
    included_files: list[str]
    total_bytes: int
    truncation_applied: bool


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(data, encoding="utf-8")
    os.replace(tmp_path, path)


def _strip_diff_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    if path.startswith("./"):
        return path[2:]
    return path


def _matches_exclude(rel_path: Path, patterns: list[str]) -> Optional[str]:
    rel = rel_path.as_posix()
    for pattern in patterns:
        if fnmatch.fnmatch(rel, pattern):
            return pattern
    return None


def _resolve_path(repo_dir: Path, raw_path: str) -> Optional[Path]:
    if not raw_path:
        return None
    cleaned = _strip_diff_prefix(raw_path)
    candidate = Path(cleaned)
    if candidate.is_absolute():
        try:
            candidate.relative_to(repo_dir)
            return candidate if candidate.exists() else None
        except Exception:
            pass
    resolved = repo_dir / cleaned
    if resolved.exists():
        return resolved
    if repo_dir.name in candidate.parts:
        parts = list(candidate.parts)
        idx = parts.index(repo_dir.name)
        alt = repo_dir.joinpath(*parts[idx + 1 :])
        if alt.exists():
            return alt
    return None


def _load_gate_signals(gate_report_path: Path) -> list[dict[str, Any]]:
    report = json.loads(gate_report_path.read_text(encoding="utf-8"))
    signals: list[dict[str, Any]] = []
    for run in report.get("runs", []):
        for result in run.get("results", []):
            for sig in result.get("signals", []) or []:
                path = sig.get("path")
                line = sig.get("line")
                if not path or not isinstance(line, int):
                    continue
                signals.append({"path": path, "line": line, "tool": sig.get("tool")})
    return signals


def _load_diff(mutation_diff_path: Path) -> tuple[list[dict[str, Any]], str, Optional[str]]:
    if PatchSet is None:
        return [], "unavailable", None
    diff_text = mutation_diff_path.read_text(encoding="utf-8", errors="ignore")
    try:
        patch = PatchSet(diff_text)
    except Exception as exc:
        return [], "error", str(exc)
    files: list[dict[str, Any]] = []
    for patched_file in patch:
        file_path = _strip_diff_prefix(patched_file.path)
        hunks: list[dict[str, Any]] = []
        for hunk in patched_file:
            header = f"@@ -{hunk.source_start},{hunk.source_length} +{hunk.target_start},{hunk.target_length} @@"
            body = "".join(f"{line.line_type}{line.value}" for line in hunk)
            hunks.append(
                {
                    "source_start": hunk.source_start,
                    "source_length": hunk.source_length,
                    "target_start": hunk.target_start,
                    "target_length": hunk.target_length,
                    "hunk": f"{header}\n{body}".rstrip("\n"),
                }
            )
        files.append({"path": file_path, "hunks": hunks})
    return files, "ok", None


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ranges = sorted(ranges, key=lambda r: (r[0], r[1]))
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()


def _extract_snippet(lines: list[str], start: int, end: int) -> str:
    start = max(1, start)
    end = min(len(lines), end)
    if start > end:
        return ""
    return "\n".join(lines[start - 1 : end])


def _ast_available() -> bool:
    global _AST_AVAILABLE, _AST_PARSER
    if _AST_AVAILABLE is not None:
        return _AST_AVAILABLE
    if get_parser is None:
        _AST_AVAILABLE = False
        return False
    try:
        _AST_PARSER = get_parser("python")
        _AST_AVAILABLE = True
        return True
    except Exception:
        _AST_AVAILABLE = False
        _AST_PARSER = None
        return False


def _ast_block_for_line(path: Path, line: int) -> Optional[tuple[int, int]]:
    if not _ast_available():
        return None
    parser = _AST_PARSER
    if parser is None:
        return None
    source = path.read_bytes()
    tree = parser.parse(source)
    target_row = line - 1

    def visit(node) -> Optional[tuple[int, int]]:
        if node.type in {"function_definition", "class_definition"}:
            start_row = node.start_point[0]
            end_row = node.end_point[0]
            if start_row <= target_row <= end_row:
                return (start_row + 1, end_row + 1)
        for child in node.children:
            result = visit(child)
            if result:
                return result
        return None

    return visit(tree.root_node)


def _truncate_text(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    if max_bytes <= 0:
        return ""
    marker = "\n...<truncated>...\n"
    marker_bytes = len(marker.encode("utf-8"))
    if max_bytes <= marker_bytes:
        return encoded[:max_bytes].decode("utf-8", errors="ignore")
    keep = max_bytes - marker_bytes
    head = keep // 2
    tail = keep - head
    head_text = encoded[:head].decode("utf-8", errors="ignore")
    tail_text = encoded[-tail:].decode("utf-8", errors="ignore")
    return f"{head_text}{marker}{tail_text}"


def _collect_text_fields(bundle: dict[str, Any]) -> list[tuple[dict[str, Any], str, str]]:
    refs: list[tuple[dict[str, Any], str, str]] = []
    for entry in bundle.get("files", []):
        for snippet in entry.get("snippets", []):
            text = snippet.get("text", "")
            refs.append((snippet, "text", text))
        for hunk in entry.get("diff_hunks", []):
            text = hunk.get("hunk", "")
            refs.append((hunk, "hunk", text))
    return refs


def _apply_truncation(bundle: dict[str, Any], max_bytes: int) -> tuple[int, bool]:
    def serialized_size(payload: dict[str, Any]) -> int:
        return len(_stable_json(payload).encode("utf-8"))

    def drop_optional(payload: dict[str, Any]) -> None:
        for field in ("selection_order", "excluded_by_rule"):
            if field in payload:
                payload[field] = []

    def strip_file_payload(payload: dict[str, Any]) -> None:
        for entry in payload.get("files", []):
            entry["snippets"] = []
            entry["diff_hunks"] = []

    def strip_metadata(payload: dict[str, Any]) -> None:
        for field in ("selection_order", "excluded_by_rule", "gate_report_path", "mutation_diff_path"):
            payload.pop(field, None)
        payload["metadata_trimmed"] = True
    size = serialized_size(bundle)
    if size <= max_bytes:
        return size, False

    truncation_applied = True

    while size > max_bytes and len(bundle.get("files", [])) > 1:
        bundle["files"].pop()
        size = serialized_size(bundle)

    refs = _collect_text_fields(bundle)
    if not refs:
        return size, truncation_applied

    stripped = deepcopy(bundle)
    drop_optional(stripped)
    for ref in _collect_text_fields(stripped):
        ref[0][ref[1]] = ""

    base_size = serialized_size(stripped)
    if base_size >= max_bytes:
        drop_optional(bundle)
        for ref in refs:
            ref[0][ref[1]] = ""
        strip_file_payload(bundle)
        size = serialized_size(bundle)
        if size > max_bytes:
            strip_metadata(bundle)
            size = serialized_size(bundle)
        return size, truncation_applied

    remaining = max_bytes - base_size
    per_text = max(0, remaining // len(refs))

    def apply_limit(limit: int) -> None:
        for target, key, original in refs:
            target[key] = _truncate_text(original, limit)

    apply_limit(per_text)
    size = serialized_size(bundle)
    while size > max_bytes and per_text > 0:
        per_text = int(per_text * 0.8)
        apply_limit(per_text)
        size = serialized_size(bundle)

    return size, truncation_applied


def build_context_bundle(request: ContextPackRequest) -> ContextPackResponse:
    repo_dir = Path(request.repo_dir).resolve()
    gate_report_path = Path(request.gate_report_path)
    mutation_diff_path = None
    if request.mutation_diff_path:
        mutation_diff_path = Path(request.mutation_diff_path)
    if not repo_dir.exists() or not repo_dir.is_dir():
        raise FileNotFoundError(f"Repo dir '{repo_dir}' does not exist.")
    if not gate_report_path.exists():
        raise FileNotFoundError(f"Gate report '{gate_report_path}' does not exist.")
    if mutation_diff_path is not None and not mutation_diff_path.exists():
        raise FileNotFoundError(f"Mutation diff '{mutation_diff_path}' does not exist.")

    signals = _load_gate_signals(gate_report_path)
    if mutation_diff_path is None:
        diff_files, diff_parse_status, diff_parse_error = [], "skipped", None
    else:
        diff_files, diff_parse_status, diff_parse_error = _load_diff(mutation_diff_path)
    ast_blocks_status = "ok" if _ast_available() else "unavailable"

    file_info: dict[str, dict[str, Any]] = {}
    excluded_samples: dict[str, list[str]] = {}

    for sig in signals:
        path = sig["path"]
        line = sig["line"]
        resolved = _resolve_path(repo_dir, path)
        if not resolved:
            continue
        rel = resolved.relative_to(repo_dir)
        excluded_by = _matches_exclude(rel, EXCLUDE_PATTERNS)
        if excluded_by:
            samples = excluded_samples.setdefault(excluded_by, [])
            if len(samples) < EXCLUDE_CAP:
                samples.append(rel.as_posix())
            continue
        info = file_info.setdefault(
            rel.as_posix(),
            {"reasons": set(), "signal_lines": set(), "hunks": [], "resolved": resolved},
        )
        info["reasons"].add("gate_signal")
        info["signal_lines"].add(line)

    for diff in diff_files:
        path = diff["path"]
        resolved = _resolve_path(repo_dir, path)
        rel_path = None
        if resolved:
            rel_path = resolved.relative_to(repo_dir).as_posix()
        excluded_by = _matches_exclude(Path(rel_path), EXCLUDE_PATTERNS)
        if excluded_by:
            samples = excluded_samples.setdefault(excluded_by, [])
            if len(samples) < EXCLUDE_CAP:
                samples.append(rel_path)
            continue
        else:
            rel_path = _strip_diff_prefix(path)
            if _matches_exclude(Path(rel_path), EXCLUDE_PATTERNS):
                continue
        info = file_info.setdefault(
            rel_path,
            {"reasons": set(), "signal_lines": set(), "hunks": [], "resolved": resolved},
        )
        info["reasons"].add("mutation_diff")
        info["hunks"].extend(diff["hunks"])

    if not file_info:
        raise ValueError("No relevant files could be resolved from gate report or diff.")

    def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
        path, info = item
        priority = 0 if "mutation_diff" in info["reasons"] else 1
        return (priority, path)

    ordered_items = sorted(file_info.items(), key=sort_key)
    ordered_items = ordered_items[: request.max_files]

    files_payload: list[dict[str, Any]] = []
    for rel_path, info in ordered_items:
        resolved = info.get("resolved")
        reasons = sorted(info["reasons"])
        hunks = sorted(
            info.get("hunks", []),
            key=lambda h: (h.get("source_start", 0), h.get("target_start", 0)),
        )
        snippets: list[dict[str, Any]] = []
        if resolved and resolved.exists():
            lines = _read_lines(resolved)
            ranges_by_kind: dict[str, list[tuple[int, int]]] = {
                "gate_signal": [],
                "mutation_hunk": [],
                "ast_block": [],
            }
            for line in sorted(info.get("signal_lines", [])):
                ranges_by_kind["gate_signal"].append(
                    (line - request.context_radius_lines, line + request.context_radius_lines)
                )
                if request.include_ast_blocks and _ast_available():
                    ast_range = _ast_block_for_line(resolved, line)
                    if ast_range:
                        ranges_by_kind["ast_block"].append(ast_range)
            for hunk in hunks:
                anchor = hunk.get("target_start") or hunk.get("source_start") or 1
                ranges_by_kind["mutation_hunk"].append(
                    (anchor - request.context_radius_lines, anchor + request.context_radius_lines)
                )
                if request.include_ast_blocks and _ast_available():
                    ast_range = _ast_block_for_line(resolved, anchor)
                    if ast_range:
                        ranges_by_kind["ast_block"].append(ast_range)

            for kind, ranges in ranges_by_kind.items():
                merged = _merge_ranges([(max(1, s), max(1, e)) for s, e in ranges])
                for start, end in merged:
                    text = _extract_snippet(lines, start, end)
                    snippets.append(
                        {
                            "kind": kind,
                            "start_line": start,
                            "end_line": end,
                            "text": text,
                        }
                    )

        snippets = sorted(snippets, key=lambda s: (s["start_line"], s["end_line"], s["kind"]))
        files_payload.append(
            {
                "path": rel_path,
                "reasons": reasons,
                "snippets": snippets,
                "diff_hunks": hunks,
            }
        )

    selection_order: list[dict[str, Any]] = []
    for entry in files_payload:
        size = 0
        for snippet in entry.get("snippets", []):
            size += len(snippet.get("text", "").encode("utf-8"))
        for hunk in entry.get("diff_hunks", []):
            size += len(hunk.get("hunk", "").encode("utf-8"))
        selection_order.append(
            {"path": entry["path"], "reasons": entry.get("reasons", []), "bytes": size}
        )

    included_files = [entry["path"] for entry in files_payload]
    included_bytes = sum(entry.get("bytes", 0) for entry in selection_order)

    bundle = {
        "context_id": "0" * 64,
        "repo_dir": str(repo_dir),
        "gate_report_path": str(gate_report_path),
        "mutation_diff_path": str(mutation_diff_path) if mutation_diff_path else None,
        "max_bytes": request.max_bytes,
        "max_files": request.max_files,
        "context_radius_lines": request.context_radius_lines,
        "diff_parse_status": diff_parse_status,
        "diff_parse_error": diff_parse_error,
        "ast_blocks_status": ast_blocks_status,
        "excluded_by_rule": [
            {"pattern": pattern, "sample_paths": samples}
            for pattern, samples in sorted(excluded_samples.items())
        ],
        "selection_order": selection_order,
        "included_files_count": len(included_files),
        "included_bytes": included_bytes,
        "truncation_applied": False,
        "metadata_trimmed": False,
        "files": files_payload,
    }

    total_bytes, truncation_applied = _apply_truncation(bundle, request.max_bytes)

    bundle_for_hash = deepcopy(bundle)
    context_id = hashlib.sha256(_stable_json(bundle_for_hash).encode("utf-8")).hexdigest()
    bundle["context_id"] = context_id

    context_dir = repo_dir / ".pf_manifest" / "context"
    context_bundle_path = context_dir / f"{context_id}.json"
    _atomic_write_json(context_bundle_path, bundle)

    bundle["truncation_applied"] = truncation_applied
    total_bytes = len(_stable_json(bundle).encode("utf-8"))

    return ContextPackResponse(
        context_bundle_path=str(context_bundle_path),
        included_files=included_files,
        total_bytes=total_bytes,
        truncation_applied=truncation_applied,
    )


def main() -> None:
    payload = sys.stdin.read().strip()
    if not payload:
        raise SystemExit("Expected JSON request on stdin")
    request = ContextPackRequest(**json.loads(payload))
    response = build_context_bundle(request)
    sys.stdout.write(json.dumps(_model_dump(response), indent=2))


if __name__ == "__main__":
    main()
