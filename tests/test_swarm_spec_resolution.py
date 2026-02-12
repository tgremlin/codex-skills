from __future__ import annotations

import json
import os
from pathlib import Path

from swarm_skills import swarm_cli


def _touch(path: Path, content: str = "# spec\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _latest_resolution(repo_root: Path) -> dict[str, object]:
    path = repo_root / "artifacts" / "swarm_run" / "latest" / "spec_resolution.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_run_no_spec_exits_2_without_generate(tmp_path: Path, capsys) -> None:
    code = swarm_cli.main([
        "run",
        "--repo",
        str(tmp_path),
        "--goal",
        "add team mode",
        "--dry-run",
    ])

    assert code == 2
    out = capsys.readouterr().out
    assert "No spec found." in out

    resolution = _latest_resolution(tmp_path)
    assert resolution["mode"] == "missing_error"


def test_run_no_spec_generate_and_continue(tmp_path: Path) -> None:
    code = swarm_cli.main([
        "run",
        "--repo",
        str(tmp_path),
        "--goal",
        "add team mode",
        "--dry-run",
        "--gen-spec-if-missing",
        "--autofix",
        "--max-iterations",
        "1",
    ])

    assert code == 0
    resolution = _latest_resolution(tmp_path)
    assert resolution["mode"] == "generated"
    generated_rel = str(resolution["generated_spec"])
    assert generated_rel
    assert (tmp_path / generated_rel).exists()


def test_discovery_picks_newest_candidate_and_logs_all(tmp_path: Path) -> None:
    old_spec = _touch(tmp_path / "examples" / "specs" / "a.md")
    new_spec = _touch(tmp_path / "docs" / "specs" / "b.md")

    os.utime(old_spec, (1_700_000_000, 1_700_000_000))
    os.utime(new_spec, (1_800_000_000, 1_800_000_000))

    code = swarm_cli.main([
        "plan",
        "--repo",
        str(tmp_path),
        "--goal",
        "plan only",
    ])

    assert code == 0
    resolution = _latest_resolution(tmp_path)
    assert resolution["mode"] == "discovered"
    candidates = list(resolution["discovered_candidates"])
    assert "examples/specs/a.md" in candidates
    assert "docs/specs/b.md" in candidates
    assert resolution["chosen_spec"] == "docs/specs/b.md"


def test_spec_override_skips_discovery(tmp_path: Path) -> None:
    explicit = _touch(tmp_path / "my_spec.md")
    _touch(tmp_path / "examples" / "specs" / "other.md")

    code = swarm_cli.main([
        "plan",
        "--repo",
        str(tmp_path),
        "--goal",
        "plan only",
        "--spec",
        str(explicit),
    ])

    assert code == 0
    resolution = _latest_resolution(tmp_path)
    assert resolution["mode"] == "provided"
    assert resolution["discovered_candidates"] == []
    assert resolution["chosen_spec"] == explicit.resolve().as_posix()
