import json
import tempfile
from pathlib import Path

from container_image_build import (
    ImageBuildRequest,
    _docker_available,
    _profile_id,
    _should_retry_with_builder,
    build_container_image,
)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _profile_payload() -> dict:
    profile = {
        "status": "supported",
        "reason": None,
        "missing": [],
        "python_version_target": "3.11",
        "install_cmds": ["python -m pip install -r requirements.txt"],
        "gates": {"test": None, "lint": None, "typecheck": None},
        "decisions": {},
        "detected_tools": [],
        "suggested_profiles": [],
        "working_dir": ".",
        "env": {},
    }
    return {"profile_id": _profile_id(profile), "profile": profile}


def test_build_image_for_tiny_repo():
    if not _docker_available():
        return

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _write(repo / "requirements.txt", "pytest\n")
        (repo / "tests").mkdir()
        _write(repo / "tests" / "test_sample.py", "def test_ok():\n    assert True\n")

        manifest_dir = repo / ".pf_manifest"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        profile_path = manifest_dir / "repo_profile.json"
        profile_payload = _profile_payload()
        profile_path.write_text(json.dumps(profile_payload, indent=2), encoding="utf-8")

        request = ImageBuildRequest(repo_dir=str(repo), profile_path=str(profile_path))
        response = build_container_image(request)

        assert response.image_tag
        assert Path(response.build_log_path).exists()


def test_builder_retry_detection():
    log = (
        "yarl/_quoting.c:196:12: fatal error: longintrepr.h: No such file or directory\n"
        "error: command '/usr/bin/gcc' failed with exit code 1\n"
    )
    assert _should_retry_with_builder(log) is True
