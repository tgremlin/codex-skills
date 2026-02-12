from __future__ import annotations

import json
from pathlib import Path

import pytest

from swarm_skills.spec_discovery import (
    SpecDiscoveryError,
    discover_spec,
    find_candidates,
    read_pointer_file_json,
    read_pointer_file_txt,
)


def _touch(path: Path, content: str = "# spec\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def test_pointer_txt_resolves_relative_path(tmp_path: Path) -> None:
    workspace = tmp_path
    spec = _touch(workspace / "examples/specs/app_wizard.md")
    pointer = _touch(workspace / ".swarm/spec_path.txt", "examples/specs/app_wizard.md\n")

    resolved = read_pointer_file_txt(pointer)

    assert resolved == spec.resolve()
    assert discover_spec(workspace) == spec.resolve()


def test_pointer_json_resolves_relative_path(tmp_path: Path) -> None:
    workspace = tmp_path
    spec = _touch(workspace / "examples/specs/app_from_flow_next.md")
    pointer = workspace / ".swarm/spec.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"spec_path": "examples/specs/app_from_flow_next.md"}), encoding="utf-8")

    resolved = read_pointer_file_json(pointer)

    assert resolved == spec.resolve()
    assert discover_spec(workspace) == spec.resolve()


def test_no_pointer_single_heuristic_match_returns_spec(tmp_path: Path) -> None:
    workspace = tmp_path
    spec = _touch(workspace / "examples/specs/only_wizard.md")

    assert discover_spec(workspace) == spec.resolve()


def test_no_pointer_multiple_heuristic_matches_fails_with_sorted_candidates(tmp_path: Path) -> None:
    workspace = tmp_path
    _touch(workspace / "examples/specs/b_from_flow_next.md")
    _touch(workspace / "examples/specs/a_wizard.md")
    _touch(workspace / "spec.md")

    with pytest.raises(SpecDiscoveryError) as exc_info:
        discover_spec(workspace)

    err = exc_info.value
    assert err.reason == "ambiguous_candidates"
    rel_candidates = [_rel(path, workspace) for path in err.candidates]
    assert rel_candidates == [
        "examples/specs/a_wizard.md",
        "examples/specs/b_from_flow_next.md",
        "spec.md",
    ]


def test_no_pointer_no_matches_fails_with_guidance(tmp_path: Path) -> None:
    workspace = tmp_path

    with pytest.raises(SpecDiscoveryError) as exc_info:
        discover_spec(workspace)

    err = exc_info.value
    assert err.reason == "no_candidates"
    assert "Pass --spec explicitly" in err.guidance


def test_pointer_target_missing_fails_with_guidance(tmp_path: Path) -> None:
    workspace = tmp_path
    _touch(workspace / ".swarm/spec_path.txt", "examples/specs/missing.md\n")

    with pytest.raises(SpecDiscoveryError) as exc_info:
        discover_spec(workspace)

    err = exc_info.value
    assert err.reason == "pointer_txt_missing_target"
    assert "existing SPEC markdown" in err.guidance


def test_pointer_outside_workspace_is_rejected(tmp_path: Path) -> None:
    root = tmp_path
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    _touch(root / "outside.md")
    _touch(workspace / ".swarm/spec_path.txt", "../outside.md\n")

    with pytest.raises(SpecDiscoveryError) as exc_info:
        discover_spec(workspace)

    err = exc_info.value
    assert err.reason == "pointer_txt_outside_workspace"
    assert "inside the workspace" in err.guidance


def test_find_candidates_returns_lexicographically_sorted_paths(tmp_path: Path) -> None:
    workspace = tmp_path
    _touch(workspace / "examples/specs/z.md")
    _touch(workspace / "examples/specs/a_wizard.md")
    _touch(workspace / "SPEC.md")

    candidates = find_candidates(workspace)
    assert [_rel(path, workspace) for path in candidates] == [
        "SPEC.md",
        "examples/specs/a_wizard.md",
        "examples/specs/z.md",
    ]
