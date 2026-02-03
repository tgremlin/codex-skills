import json
from pathlib import Path

from context_pack import ContextPackRequest, build_context_bundle


def _write_file(path: Path, lines: int) -> None:
    content = [f"line {i}" for i in range(1, lines + 1)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(content), encoding="utf-8")


def _write_gate_report(path: Path, rel_path: str, line: int) -> None:
    report = {
        "runs": [
            {
                "results": [
                    {
                        "signals": [
                            {
                                "tool": "pytest",
                                "path": rel_path,
                                "line": line,
                                "message": "boom",
                            }
                        ]
                    }
                ]
            }
        ]
    }
    path.write_text(json.dumps(report), encoding="utf-8")


def _write_diff(path: Path, rel_path: str) -> None:
    diff = "\n".join(
        [
            f"--- a/{rel_path}",
            f"+++ b/{rel_path}",
            "@@ -10,1 +10,1 @@",
            "-line 10",
            "+line 10 changed",
            "",
        ]
    )
    path.write_text(diff, encoding="utf-8")


def test_context_pack_basic(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    file_path = repo_dir / "src" / "foo.py"
    _write_file(file_path, 40)

    gate_report = repo_dir / "gate_report.json"
    _write_gate_report(gate_report, "src/foo.py", 10)

    diff_path = repo_dir / "mut.diff"
    _write_diff(diff_path, "src/foo.py")

    request = ContextPackRequest(
        repo_dir=str(repo_dir),
        gate_report_path=str(gate_report),
        mutation_diff_path=str(diff_path),
        max_bytes=10000,
        max_files=5,
        context_radius_lines=5,
    )
    response = build_context_bundle(request)
    assert response.included_files == ["src/foo.py"]
    assert response.truncation_applied is False
    assert Path(response.context_bundle_path).exists()


def test_context_pack_truncation(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    file_path = repo_dir / "src" / "foo.py"
    _write_file(file_path, 200)

    gate_report = repo_dir / "gate_report.json"
    _write_gate_report(gate_report, "src/foo.py", 120)

    diff_path = repo_dir / "mut.diff"
    _write_diff(diff_path, "src/foo.py")

    request = ContextPackRequest(
        repo_dir=str(repo_dir),
        gate_report_path=str(gate_report),
        mutation_diff_path=str(diff_path),
        max_bytes=600,
        max_files=1,
        context_radius_lines=50,
    )
    response = build_context_bundle(request)
    assert response.truncation_applied is True
    assert response.total_bytes <= 600


def test_context_pack_without_diff(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    file_path = repo_dir / "src" / "foo.py"
    _write_file(file_path, 20)

    gate_report = repo_dir / "gate_report.json"
    _write_gate_report(gate_report, "src/foo.py", 5)

    request = ContextPackRequest(
        repo_dir=str(repo_dir),
        gate_report_path=str(gate_report),
        mutation_diff_path=None,
        max_bytes=10000,
        max_files=5,
        context_radius_lines=5,
    )
    response = build_context_bundle(request)
    assert response.included_files == ["src/foo.py"]
    bundle = json.loads(Path(response.context_bundle_path).read_text(encoding="utf-8"))
    assert bundle.get("diff_parse_status") == "skipped"
