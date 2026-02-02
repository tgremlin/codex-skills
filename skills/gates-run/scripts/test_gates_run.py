import json
import tempfile
from pathlib import Path

from gates_run import GatesRunRequest, _docker_available, run_gates

try:
    import docker
except Exception:
    docker = None


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_image(repo: Path, tag: str) -> None:
    if docker is None:
        raise RuntimeError("docker SDK not available")
    dockerfile = repo / "Dockerfile"
    _write(
        dockerfile,
        """
FROM python:3.11-slim
WORKDIR /workspace
COPY requirements.txt /workspace/requirements.txt
RUN python -m pip install -r requirements.txt
""".strip()
        + "\n",
    )

    client = docker.from_env()
    output = client.api.build(
        path=str(repo),
        dockerfile="Dockerfile",
        tag=tag,
        decode=True,
        rm=True,
        forcerm=True,
    )
    for entry in output:
        if "error" in entry:
            raise RuntimeError(entry["error"])


def _write_profile(repo: Path) -> Path:
    manifest_dir = repo / ".pf_manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    profile_path = manifest_dir / "repo_profile.json"
    profile = {
        "status": "supported",
        "reason": None,
        "missing": [],
        "python_version_target": "3.11",
        "install_cmds": ["python -m pip install -r requirements.txt"],
        "gates": {"test": {"cmd": "pytest", "timeout_sec": 1200}},
        "decisions": {},
        "detected_tools": [],
        "suggested_profiles": [],
        "working_dir": ".",
        "env": {},
    }
    payload = {"profile_id": "test-profile", "profile": profile}
    profile_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return profile_path


def test_gates_run_pass_then_fail():
    if not _docker_available():
        return

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo / "requirements.txt", "pytest\n")
        _write(repo / "tests" / "test_ok.py", "def test_ok():\n    assert True\n")

        profile_path = _write_profile(repo)
        image_tag = "gates-run-test:latest"
        _build_image(repo, image_tag)

        request = GatesRunRequest(
            repo_dir=str(repo),
            image_tag=image_tag,
            profile_path=str(profile_path),
        )
        response = run_gates(request)
        report = json.loads(Path(response.gate_report_path).read_text(encoding="utf-8"))
        summary = report["summary"]
        assert summary["fail_count"] == 0
        assert summary["pass_count"] >= 1

        _write(repo / "tests" / "test_ok.py", "def test_ok():\n    assert False\n")
        response = run_gates(request)
        report = json.loads(Path(response.gate_report_path).read_text(encoding="utf-8"))
        summary = report["summary"]
        assert summary["fail_count"] >= 1
        signals = report["runs"][0]["results"][0]["signals"]
        assert any("tests/test_ok.py" in signal["path"] for signal in signals)
