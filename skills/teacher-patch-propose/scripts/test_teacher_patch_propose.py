import json
import os
import sys
from pathlib import Path

from teacher_patch_propose import (
    PatchConstraints,
    TeacherPatchRequest,
    propose_patch,
)


def _write_context_bundle(path: Path, repo_dir: Path) -> None:
    bundle = {
        "context_id": "ctx",
        "repo_dir": str(repo_dir),
        "files": [
            {
                "path": "src/app.py",
                "reasons": ["gate_signal"],
                "snippets": [
                    {
                        "kind": "gate_signal",
                        "start_line": 1,
                        "end_line": 3,
                        "text": "line1\nline2\nline3",
                    }
                ],
                "diff_hunks": [],
            }
        ],
    }
    path.write_text(json.dumps(bundle), encoding="utf-8")


def _write_repo(repo_dir: Path) -> None:
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "tests").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (repo_dir / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (repo_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")


def _write_provider(tmp_path: Path) -> None:
    provider_code = """
import os

def generate(prompt, model_id, attempt, context):
    return os.environ.get("TEACHER_OUTPUT", "")
"""
    provider_path = tmp_path / "provider_stub.py"
    provider_path.write_text(provider_code, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    os.environ["TEACHER_PROVIDER"] = "provider_stub:generate"


def test_invalid_output(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _write_repo(repo_dir)
    context_path = tmp_path / "context.json"
    _write_context_bundle(context_path, repo_dir)
    _write_provider(tmp_path)

    os.environ["TEACHER_OUTPUT"] = "not a diff"

    request = TeacherPatchRequest(
        context_bundle_path=str(context_path),
        constraints=PatchConstraints(),
        model_id="teacher",
        attempt=1,
    )
    response = propose_patch(request)
    assert response.is_valid_diff is False
    assert "not_unified_diff" in response.validation_errors
    assert Path(response.patch_text_path).read_text(encoding="utf-8") == ""


def test_valid_diff(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _write_repo(repo_dir)
    context_path = tmp_path / "context.json"
    _write_context_bundle(context_path, repo_dir)
    _write_provider(tmp_path)

    diff = """
--- a/src/app.py
+++ b/src/app.py
@@ -1,1 +1,1 @@
-print('hi')
+print('hello')
""".strip()
    os.environ["TEACHER_OUTPUT"] = diff

    request = TeacherPatchRequest(
        context_bundle_path=str(context_path),
        constraints=PatchConstraints(max_files_changed=1, max_lines_changed=4),
        model_id="teacher",
        attempt=1,
    )
    response = propose_patch(request)
    assert response.is_valid_diff is True
    assert response.validation_errors == []
    assert Path(response.patch_text_path).read_text(encoding="utf-8").strip().startswith("--- ")


def test_recount_hunks_on_invalid_counts(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _write_repo(repo_dir)
    context_path = tmp_path / "context.json"
    _write_context_bundle(context_path, repo_dir)
    _write_provider(tmp_path)

    diff = """
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
-print('hi')
+print('hello')
""".strip()
    os.environ["TEACHER_OUTPUT"] = diff

    request = TeacherPatchRequest(
        context_bundle_path=str(context_path),
        constraints=PatchConstraints(max_files_changed=1, max_lines_changed=4),
        model_id="teacher",
        attempt=3,
    )
    response = propose_patch(request)
    assert response.is_valid_diff is True
    assert response.validation_errors == []
    assert Path(response.patch_text_path).read_text(encoding="utf-8").strip().startswith("--- ")

def test_markdown_fenced_diff(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _write_repo(repo_dir)
    context_path = tmp_path / "context.json"
    _write_context_bundle(context_path, repo_dir)
    _write_provider(tmp_path)

    diff = (
        "```diff\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-print('hi')\n"
        "+print('hola')\n"
        "```"
    )
    os.environ["TEACHER_OUTPUT"] = diff

    request = TeacherPatchRequest(
        context_bundle_path=str(context_path),
        constraints=PatchConstraints(max_files_changed=1, max_lines_changed=4),
        model_id="teacher",
        attempt=2,
    )
    response = propose_patch(request)
    assert response.is_valid_diff is True
    assert response.validation_errors == []
    assert Path(response.patch_text_path).read_text(encoding="utf-8").strip().startswith("--- ")


def test_constraints_rejected(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _write_repo(repo_dir)
    context_path = tmp_path / "context.json"
    _write_context_bundle(context_path, repo_dir)
    _write_provider(tmp_path)

    diff = """
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -1,2 +1,2 @@
-def test_ok():
+def test_ok2():
     assert True
""".strip()
    os.environ["TEACHER_OUTPUT"] = diff

    request = TeacherPatchRequest(
        context_bundle_path=str(context_path),
        constraints=PatchConstraints(max_files_changed=1, max_lines_changed=4, allow_tests_edit=False),
        model_id="teacher",
        attempt=1,
    )
    response = propose_patch(request)
    assert response.is_valid_diff is False
    assert "tests_edit_not_allowed" in response.validation_errors


def test_invalid_hunk_header_rejected(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _write_repo(repo_dir)
    context_path = tmp_path / "context.json"
    _write_context_bundle(context_path, repo_dir)
    _write_provider(tmp_path)

    diff = """
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@
-print('hi')
+print('bye')
""".strip()
    os.environ["TEACHER_OUTPUT"] = diff

    request = TeacherPatchRequest(
        context_bundle_path=str(context_path),
        constraints=PatchConstraints(max_files_changed=1, max_lines_changed=4),
        model_id="teacher",
        attempt=4,
    )
    response = propose_patch(request)
    assert response.is_valid_diff is False
    assert "invalid_hunk_header" in response.validation_errors
