from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

try:
    from pydantic import BaseModel, Field, ConfigDict
except Exception:  # pragma: no cover - pydantic v1
    from pydantic import BaseModel, Field
    ConfigDict = None


DEFAULT_TMP_DIR_NAME = ".tmp-test"


class BugsInPyAdapterRequest(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    bugsinpy_root: str = Field(..., description="Path to BugsInPy root")
    project_name: str = Field(..., description="BugsInPy project name")
    bug_id: str = Field(..., description="Bug id")
    variant: str = Field(..., description="buggy or fixed")


class BugsInPyAdapterResponse(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    resolved_project_dir: str
    install_cmds: list[str]
    repo_setup_cmds: list[str]
    test_cmds: list[str]
    env: dict[str, str]
    provenance: dict[str, Any]


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _tmp_root(root: Path) -> Path:
    env_dir = os.environ.get("PF_TMP_DIR")
    if env_dir:
        return Path(env_dir)
    return root / DEFAULT_TMP_DIR_NAME


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return [line.strip() for line in raw if line.strip()]


def _parse_bug_info(path: Path) -> dict[str, str]:
    info: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"Missing bug.info at {path}")
    for line in _read_lines(path):
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"')
        info[key] = value
    return info


def _pythonpath_env(pythonpath_value: str, project_dir: Path) -> Optional[str]:
    if not pythonpath_value:
        return None
    parts = [part for part in pythonpath_value.split(";") if part.strip()]
    resolved = [str(project_dir / part) for part in parts]
    return ":".join(resolved)


def _install_cmds(work_dir: Path) -> list[str]:
    requirements = work_dir / "bugsinpy_requirements.txt"

    cmds: list[str] = []
    if requirements.exists() and requirements.read_text(encoding="utf-8", errors="ignore").strip():
        cmds.append("python -m pip install -r bugsinpy_requirements.txt")

    return cmds


def _setup_cmds(work_dir: Path) -> list[str]:
    setup_script = work_dir / "bugsinpy_setup.sh"
    return _read_lines(setup_script)


def _test_cmds(work_dir: Path) -> list[str]:
    run_test = work_dir / "bugsinpy_run_test.sh"
    return _read_lines(run_test)


def _expected_commit(info: dict[str, str], variant: str) -> Optional[str]:
    if variant == "buggy":
        return info.get("buggy_commit_id")
    if variant == "fixed":
        return info.get("fixed_commit_id")
    return None


def _resolve_variant(variant: str) -> str:
    variant = variant.lower().strip()
    if variant not in {"buggy", "fixed"}:
        raise ValueError("variant must be 'buggy' or 'fixed'")
    return variant


def _bugsinpy_checkout(
    bugsinpy_root: Path,
    project_name: str,
    bug_id: str,
    variant: str,
    work_dir: Path,
) -> Path:
    framework_bin = bugsinpy_root / "framework" / "bin" / "bugsinpy-checkout"
    if not framework_bin.exists():
        raise FileNotFoundError(f"Missing bugsinpy-checkout at {framework_bin}")

    version_id = "0" if variant == "buggy" else "1"
    work_dir.mkdir(parents=True, exist_ok=True)

    target_dir = work_dir / project_name
    if target_dir.exists():
        shutil.rmtree(target_dir)

    cmd = [
        str(framework_bin),
        "-p",
        project_name,
        "-i",
        bug_id,
        "-v",
        version_id,
        "-w",
        str(work_dir),
    ]
    subprocess.run(cmd, check=True)

    if not target_dir.exists():
        raise RuntimeError("Checkout did not create project directory")
    return target_dir


def _git_head(path: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def adapt_bugsinpy(request: BugsInPyAdapterRequest) -> BugsInPyAdapterResponse:
    bugsinpy_root = Path(request.bugsinpy_root).resolve()
    project_name = request.project_name
    bug_id = request.bug_id
    variant = _resolve_variant(request.variant)

    project_root = bugsinpy_root / "projects" / project_name
    bug_dir = project_root / "bugs" / bug_id
    bug_info_path = bug_dir / "bug.info"

    info = _parse_bug_info(bug_info_path)

    work_root = _tmp_root(bugsinpy_root) / "bugsinpy" / project_name / bug_id / variant
    resolved_project_dir = _bugsinpy_checkout(
        bugsinpy_root, project_name, bug_id, variant, work_root
    )

    install_cmds = _install_cmds(resolved_project_dir)
    repo_setup_cmds = _setup_cmds(resolved_project_dir)
    test_cmds = _test_cmds(resolved_project_dir)

    env: dict[str, str] = {}
    pythonpath_value = info.get("pythonpath", "")
    pythonpath = _pythonpath_env(pythonpath_value, resolved_project_dir)
    if pythonpath:
        env["PYTHONPATH"] = pythonpath

    expected_commit = _expected_commit(info, variant)
    actual_head = _git_head(resolved_project_dir)

    provenance = {
        "bugsinpy_root": str(bugsinpy_root),
        "project_name": project_name,
        "bug_id": bug_id,
        "variant": variant,
        "buggy_commit_id": info.get("buggy_commit_id"),
        "fixed_commit_id": info.get("fixed_commit_id"),
        "expected_commit": expected_commit,
        "actual_head": actual_head,
        "python_version": info.get("python_version"),
        "bug_dir": str(bug_dir),
        "work_dir": str(work_root),
    }

    return BugsInPyAdapterResponse(
        resolved_project_dir=str(resolved_project_dir),
        install_cmds=install_cmds,
        repo_setup_cmds=repo_setup_cmds,
        test_cmds=test_cmds,
        env=env,
        provenance=provenance,
    )


if __name__ == "__main__":
    raw = json.loads(sys.stdin.read())
    req = BugsInPyAdapterRequest(**raw)
    resp = adapt_bugsinpy(req)
    print(json.dumps(_model_dump(resp), indent=2))
