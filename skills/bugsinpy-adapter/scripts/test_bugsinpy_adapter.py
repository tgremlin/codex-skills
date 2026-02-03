import tempfile
from pathlib import Path

from bugsinpy_adapter import (
    _install_cmds,
    _parse_bug_info,
    _pythonpath_env,
    _setup_cmds,
    _test_cmds,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_parse_bug_info():
    with tempfile.TemporaryDirectory() as tmp:
        bug_info = Path(tmp) / "bug.info"
        _write(
            bug_info,
            """
python_version="3.8.3"
buggy_commit_id="abc"
fixed_commit_id="def"
pythonpath="src;lib"
""".strip()
            + "\n",
        )
        info = _parse_bug_info(bug_info)
        assert info["python_version"] == "3.8.3"
        assert info["buggy_commit_id"] == "abc"
        assert info["fixed_commit_id"] == "def"
        assert info["pythonpath"] == "src;lib"


def test_install_and_test_cmds():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        _write(work_dir / "bugsinpy_requirements.txt", "requests\n")
        _write(work_dir / "bugsinpy_setup.sh", "echo setup\n")
        _write(work_dir / "bugsinpy_run_test.sh", "python -m unittest -q tests.test\n")

        install_cmds = _install_cmds(work_dir)
        assert install_cmds[0].startswith("python -m pip install -r")

        setup_cmds = _setup_cmds(work_dir)
        assert setup_cmds == ["echo setup"]

        test_cmds = _test_cmds(work_dir)
        assert test_cmds == ["python -m unittest -q tests.test"]


def test_pythonpath_env():
    with tempfile.TemporaryDirectory() as tmp:
        project_dir = Path(tmp) / "proj"
        project_dir.mkdir(parents=True, exist_ok=True)
        value = "src;lib"
        env = _pythonpath_env(value, project_dir)
        assert str(project_dir / "src") in env
        assert str(project_dir / "lib") in env
