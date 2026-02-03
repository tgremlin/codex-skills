import json
from pathlib import Path

from patch_validate_apply import PatchApplyRequest, PatchPolicy, validate_and_apply


def _write_repo(repo_dir: Path) -> None:
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "tests").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (repo_dir / "tests" / "test_app.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )


def test_apply_valid_patch(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _write_repo(repo_dir)

    patch_text = """
--- a/src/app.py
+++ b/src/app.py
@@ -1,1 +1,1 @@
-print('hi')
+print('hello')
""".strip()
    patch_path = tmp_path / "patch.diff"
    patch_path.write_text(patch_text, encoding="utf-8")

    request = PatchApplyRequest(
        repo_dir=str(repo_dir),
        patch_text_path=str(patch_path),
        policy=PatchPolicy(max_files_changed=1, max_lines_changed=4),
        fail_on_suspicious=True,
    )
    response = validate_and_apply(request)
    assert response.applied is True
    assert response.files_changed == 1
    assert response.lines_changed == 2
    assert response.suspicious_findings == []
    report = json.loads(Path(response.apply_report_path).read_text(encoding="utf-8"))
    assert report["apply"]["applied"] is True


def test_reject_tests_edit_by_default(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _write_repo(repo_dir)

    patch_text = """
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -1,2 +1,2 @@
-def test_ok():
+def test_ok2():
     assert True
""".strip()
    patch_path = tmp_path / "patch.diff"
    patch_path.write_text(patch_text, encoding="utf-8")

    request = PatchApplyRequest(
        repo_dir=str(repo_dir),
        patch_text_path=str(patch_path),
        policy=PatchPolicy(max_files_changed=1, max_lines_changed=4, allow_tests_edit=False),
        fail_on_suspicious=True,
    )
    response = validate_and_apply(request)
    assert response.applied is False
    report = json.loads(Path(response.apply_report_path).read_text(encoding="utf-8"))
    assert "tests_edit_not_allowed" in report["validation_errors"]
