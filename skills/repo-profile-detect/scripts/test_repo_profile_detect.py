import json
import tempfile
from pathlib import Path

from repo_profile_detect import RepoProfileRequest, detect_repo_profile


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_requirements_project_uses_pip_and_pytest():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo / "requirements.txt", "pytest\n")
        (repo / "tests").mkdir()
        request = RepoProfileRequest(repo_dir=str(repo))
        response = detect_repo_profile(request)
        profile = response.profile

        assert profile.status == "supported"
        assert profile.install_cmds == ["python -m pip install -r requirements.txt"]
        assert profile.gates.test is not None
        assert profile.gates.test.cmd == "pytest"
        assert profile.working_dir == "."


def test_pyproject_with_poetry_lock_uses_poetry():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(
            repo / "pyproject.toml",
            """
[tool.poetry]
name = "demo"
version = "0.1.0"
[tool.poetry.dependencies]
python = ">=3.11"
""".strip(),
        )
        _write(repo / "poetry.lock", "")
        request = RepoProfileRequest(repo_dir=str(repo))
        response = detect_repo_profile(request)
        profile = response.profile

        assert profile.install_cmds == ["poetry install"]


def test_pyproject_with_uv_lock_uses_uv():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(
            repo / "pyproject.toml",
            """
[project]
name = "demo"
version = "0.1.0"
""".strip(),
        )
        _write(repo / "uv.lock", "")
        request = RepoProfileRequest(repo_dir=str(repo))
        response = detect_repo_profile(request)
        profile = response.profile

        assert profile.install_cmds == ["uv sync --all-extras --dev"]


def test_pyproject_without_lock_uses_pip_editable():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(
            repo / "pyproject.toml",
            """
[project]
name = "demo"
version = "0.1.0"
""".strip(),
        )
        request = RepoProfileRequest(repo_dir=str(repo))
        response = detect_repo_profile(request)
        profile = response.profile

        assert profile.install_cmds == ["python -m pip install -e ."]


def test_ruff_config_enables_lint_gate():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo / "requirements.txt", "ruff\n")
        _write(repo / "ruff.toml", "line-length = 88\n")
        request = RepoProfileRequest(repo_dir=str(repo))
        response = detect_repo_profile(request)
        profile = response.profile

        assert profile.gates.lint is not None
        assert profile.gates.lint.cmd == "ruff check ."


def test_ruff_dependency_without_config_enables_lint_gate():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo / "requirements.txt", "ruff\n")
        request = RepoProfileRequest(repo_dir=str(repo))
        response = detect_repo_profile(request)
        profile = response.profile

        assert profile.gates.lint is not None
        assert profile.gates.lint.cmd == "ruff check ."


def test_no_tests_directory_disables_test_gate():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo / "requirements.txt", "")
        request = RepoProfileRequest(repo_dir=str(repo))
        response = detect_repo_profile(request)
        profile = response.profile

        assert profile.gates.test is None
        assert profile.decisions.tests.source == "none"


def test_suggested_profiles_include_tox_and_make():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo / "tox.ini", "[tox]\n")
        _write(repo / "Makefile", "test:\n\t@echo ok\n")
        request = RepoProfileRequest(repo_dir=str(repo))
        response = detect_repo_profile(request)
        profile = response.profile

        names = [item.name for item in profile.suggested_profiles]
        assert names == ["make", "tox"]


def test_overrides_affect_profile_id():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo / "requirements.txt", "pytest\n")
        base_request = RepoProfileRequest(repo_dir=str(repo))
        base_response = detect_repo_profile(base_request)

        override_request = RepoProfileRequest(
            repo_dir=str(repo),
            overrides={"lint_cmd": "ruff check src", "env": {"X": "1"}},
        )
        override_response = detect_repo_profile(override_request)

        assert base_response.profile_id != override_response.profile_id


def test_profile_file_written():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo / "requirements.txt", "pytest\n")
        request = RepoProfileRequest(repo_dir=str(repo))
        response = detect_repo_profile(request)

        profile_path = Path(response.profile_path)
        assert profile_path.exists()
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        assert payload["profile_id"] == response.profile_id
        assert "profile_runtime" in payload
