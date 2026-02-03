import json
import tempfile
from pathlib import Path

from mutation_apply import MutationApplyRequest, MutationLimits, apply_mutation


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_flip_comparison_mutation_applies():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        target = repo / "src" / "mod.py"
        _write(
            target,
            """

def is_equal(a, b):
    return a == b
""".strip()
            + "\n",
        )

        request = MutationApplyRequest(
            repo_dir=str(repo),
            seed=7,
            operator_id="flip_comparison",
            target_file="src/mod.py",
        )
        response = apply_mutation(request)

        assert response.applied is True
        assert response.changed_lines > 0
        diff_path = Path(response.diff_path)
        assert diff_path.exists()
        mutated = target.read_text(encoding="utf-8")
        assert "!=" in mutated


def test_limits_enforced():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        target = repo / "mod.py"
        _write(
            target,
            """

def is_equal(a, b):
    return a == b
""".strip()
            + "\n",
        )

        request = MutationApplyRequest(
            repo_dir=str(repo),
            seed=1,
            operator_id="flip_comparison",
            target_file="mod.py",
            limits=MutationLimits(max_files_changed=1, max_lines_changed=0),
        )
        response = apply_mutation(request)
        assert response.applied is False

        metadata = json.loads(
            (repo / ".pf_manifest" / "mutations" / f"{response.mutation_id}.json").read_text(
                encoding="utf-8"
            )
        )
        assert metadata["applied"] is False
        assert metadata["reason"] == "limit_exceeded"


def test_default_excludes_skip_skills():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(
            repo / "skills" / "tool.py",
            "def tool(a, b):\n    return a == b\n",
        )
        _write(
            repo / "src" / "app.py",
            "def app(a, b):\n    return a == b\n",
        )

        request = MutationApplyRequest(
            repo_dir=str(repo),
            seed=4,
            operator_id="flip_comparison",
        )
        response = apply_mutation(request)
        assert response.target_file == "src/app.py"


def test_hard_deny_paths_reject_target():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        path = repo / ".pf_manifest" / "note.py"
        _write(path, "def nope():\n    return True\n")

        request = MutationApplyRequest(
            repo_dir=str(repo),
            seed=1,
            operator_id="negate_boolean",
            target_file=str(path),
        )
        try:
            apply_mutation(request)
            assert False, "Expected hard deny to raise"
        except ValueError as exc:
            assert "hard-deny" in str(exc)


def test_no_applicable_sites_reason():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        target = repo / "src" / "noop.py"
        _write(target, "def noop():\n    return 1\n")

        request = MutationApplyRequest(
            repo_dir=str(repo),
            seed=3,
            operator_id="flip_comparison",
            target_file="src/noop.py",
        )
        response = apply_mutation(request)
        assert response.applied is False
        assert response.reason == "no_applicable_sites"
        assert response.diff_path == ""
