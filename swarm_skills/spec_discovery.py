from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class SpecDiscoveryError(Exception):
    def __init__(
        self,
        *,
        reason: str,
        guidance: str,
        candidates: list[Path] | None = None,
        detail: str | None = None,
    ) -> None:
        self.reason = reason
        self.guidance = guidance
        self.candidates = candidates or []
        self.detail = detail or guidance
        super().__init__(self.detail)


def _normalize_rel(path: Path, workspace_root: Path) -> str:
    return path.resolve().relative_to(workspace_root.resolve()).as_posix()


def _workspace_from_pointer(pointer_path: Path) -> Path:
    return pointer_path.resolve().parent.parent


def _resolve_pointer_target(workspace_root: Path, raw_value: str, pointer_type: str) -> Path:
    value = raw_value.strip()
    if not value:
        raise SpecDiscoveryError(
            reason=f"{pointer_type}_invalid",
            guidance="Set the pointer file to one relative SPEC markdown path.",
            detail="Pointer file is empty.",
        )

    raw_path = Path(value)
    if raw_path.is_absolute():
        raise SpecDiscoveryError(
            reason=f"{pointer_type}_invalid",
            guidance="Use a relative path in the pointer file (example: examples/specs/app_wizard.md).",
            detail="Pointer path must be relative to the workspace root.",
        )

    resolved = (workspace_root / raw_path).resolve()
    if not resolved.is_relative_to(workspace_root):
        raise SpecDiscoveryError(
            reason=f"{pointer_type}_outside_workspace",
            guidance="Choose a spec file inside the workspace and update the pointer file.",
            detail="Pointer path resolves outside the workspace root.",
        )

    if not resolved.exists() or not resolved.is_file():
        raise SpecDiscoveryError(
            reason=f"{pointer_type}_missing_target",
            guidance="Update the pointer file to an existing SPEC markdown path inside the workspace.",
            detail="Pointer path does not exist.",
        )

    return resolved


def read_pointer_file_txt(path: Path) -> Optional[Path]:
    if not path.exists():
        return None

    workspace_root = _workspace_from_pointer(path)
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != 1:
        raise SpecDiscoveryError(
            reason="pointer_txt_invalid",
            guidance=".swarm/spec_path.txt must contain exactly one relative path.",
            detail=".swarm/spec_path.txt must contain exactly one non-empty line.",
        )

    return _resolve_pointer_target(workspace_root, lines[0], "pointer_txt")


def read_pointer_file_json(path: Path) -> Optional[Path]:
    if not path.exists():
        return None

    workspace_root = _workspace_from_pointer(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecDiscoveryError(
            reason="pointer_json_invalid",
            guidance=".swarm/spec.json must be valid JSON with {\"spec_path\": \"relative/path.md\"}.",
            detail=f"Invalid JSON in .swarm/spec.json: {exc}",
        ) from exc

    raw_value = payload.get("spec_path") if isinstance(payload, dict) else None
    if not isinstance(raw_value, str):
        raise SpecDiscoveryError(
            reason="pointer_json_invalid",
            guidance=".swarm/spec.json must contain a string field: spec_path.",
            detail="Missing or invalid `spec_path` in .swarm/spec.json.",
        )

    return _resolve_pointer_target(workspace_root, raw_value, "pointer_json")


def find_candidates(workspace_root: Path) -> list[Path]:
    root = workspace_root.resolve()
    patterns = [
        "examples/specs/*_wizard.md",
        "examples/specs/*_from_flow_next.md",
        "examples/specs/*.md",
    ]

    candidates: dict[str, Path] = {}

    for pattern in patterns:
        for path in sorted(root.glob(pattern), key=lambda item: item.as_posix()):
            resolved = path.resolve()
            if not resolved.exists() or not resolved.is_file() or not resolved.is_relative_to(root):
                continue
            rel = resolved.relative_to(root).as_posix()
            candidates[rel] = resolved

    for name in ("SPEC.md", "spec.md"):
        path = (root / name).resolve()
        if path.exists() and path.is_file() and path.is_relative_to(root):
            rel = path.relative_to(root).as_posix()
            candidates[rel] = path

    return [candidates[key] for key in sorted(candidates)]


def discover_spec(workspace_root: Path) -> Path:
    root = workspace_root.resolve()

    txt_pointer = root / ".swarm" / "spec_path.txt"
    json_pointer = root / ".swarm" / "spec.json"

    if txt_pointer.exists():
        resolved = read_pointer_file_txt(txt_pointer)
        if resolved is not None:
            return resolved

    if json_pointer.exists():
        resolved = read_pointer_file_json(json_pointer)
        if resolved is not None:
            return resolved

    candidates = find_candidates(root)
    if len(candidates) == 1:
        return candidates[0]

    if len(candidates) == 0:
        raise SpecDiscoveryError(
            reason="no_candidates",
            guidance=(
                "No SPEC was found. Pass --spec explicitly, or create .swarm/spec_path.txt with one "
                "relative path (for example: examples/specs/app_wizard.md)."
            ),
            candidates=[],
            detail=(
                "Searched in order: examples/specs/*_wizard.md, examples/specs/*_from_flow_next.md, "
                "examples/specs/*.md, SPEC.md, spec.md"
            ),
        )

    raise SpecDiscoveryError(
        reason="ambiguous_candidates",
        guidance=(
            "Multiple SPEC candidates were found. Pass --spec explicitly, or create .swarm/spec_path.txt "
            "to choose one deterministically."
        ),
        candidates=candidates,
        detail="Multiple SPEC files matched discovery patterns.",
    )
