from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

try:
    from pydantic import BaseModel, Field, ConfigDict
except Exception:  # pragma: no cover - pydantic v1
    from pydantic import BaseModel, Field
    ConfigDict = None


DEFAULT_TEST_TIMEOUT_SEC = 1200
DEFAULT_LINT_TIMEOUT_SEC = 600
DEFAULT_TYPECHECK_TIMEOUT_SEC = 600
DEFAULT_TMP_DIR_NAME = ".tmp-test"


class Overrides(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    install_cmds: Optional[list[str]] = None
    test_cmd: Optional[str] = None
    lint_cmd: Optional[str] = None
    type_cmd: Optional[str] = None
    repo_setup_cmds: Optional[list[str]] = None
    repo_setup_idempotency_check: Optional[str] = None
    repo_setup_continue_on_failure: Optional[bool] = None
    repo_setup_allow_unsafe: Optional[bool] = None
    allow_editable_install: Optional[bool] = None
    allow_unauthenticated_apt: Optional[bool] = None
    policy_profile: Optional[str] = None
    env: Optional[dict[str, str]] = None


class RepoProfileRequest(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    repo_dir: str = Field(..., description="Checked-out repository directory")
    explicit_python_version: Optional[str] = Field(
        None, description="Optional explicit Python version target"
    )
    overrides: Optional[Overrides] = None


class Gate(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    cmd: str
    timeout_sec: int


class GateProfile(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    test: Optional[Gate] = None
    lint: Optional[Gate] = None
    typecheck: Optional[Gate] = None


class Decision(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    value: Optional[str] = None
    reason: Optional[str] = None
    source: str
    defaulted_cmd: Optional[bool] = None


class RepoDecisions(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    python_version: Decision
    install: Decision
    tests: Decision
    lint: Decision
    typecheck: Decision


class RepoProfile(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    status: str
    reason: Optional[str] = None
    missing: list[str]
    project_root: str
    tests_root: Optional[str] = None
    python_version_target: Optional[str] = None
    install_cmds: list[str]
    repo_setup_cmds: list[str]
    repo_setup_idempotency_check: str
    repo_setup_continue_on_failure: bool
    repo_setup_allow_unsafe: bool
    allow_editable_install: bool
    allow_unauthenticated_apt: bool
    policy_profile: Optional[str] = None
    gates: GateProfile
    decisions: RepoDecisions
    detected_tools: list[str]
    suggested_profiles: list["SuggestedProfile"]
    working_dir: str
    env: dict[str, str]


class RepoProfileRuntime(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    working_dir_abs: str
    repo_dir_abs: str


class SuggestedProfile(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    name: str
    reason: str
    install_cmds: list[str]
    gates: GateProfile


class RepoProfileResponse(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    profile_id: str
    profile: RepoProfile
    profile_runtime: RepoProfileRuntime
    profile_path: str


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _tmp_root(repo_dir: Path) -> Path:
    env_dir = os.environ.get("PF_TMP_DIR")
    if env_dir:
        return Path(env_dir)
    return repo_dir / DEFAULT_TMP_DIR_NAME


def _read_first_line(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    content = _read_text(path).splitlines()
    if not content:
        return None
    line = content[0].strip()
    return line or None


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists() or tomllib is None:
        return {}
    return tomllib.loads(_read_text(path))


def _load_cfg(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read_string(_read_text(path))
    return parser


def _normalize_req_line(line: str) -> str:
    stripped = line.split("#", 1)[0].strip()
    return stripped


def _normalize_cmd_list(cmds: list[str]) -> list[str]:
    return [cmd.strip() for cmd in cmds if cmd and cmd.strip()]


def _extract_req_name(req_line: str) -> str:
    match = re.match(r"^([A-Za-z0-9_.-]+)", req_line)
    return match.group(1).lower() if match else ""


def _requirements_have_dep(lines: list[str], dep: str) -> bool:
    dep = dep.lower()
    for line in lines:
        normalized = _normalize_req_line(line)
        if not normalized:
            continue
        if _extract_req_name(normalized) == dep:
            return True
    return False


def _pep508_match(req: str, dep: str) -> bool:
    dep = dep.lower()
    prefix = req.strip().lower()
    if not prefix:
        return False
    return re.match(rf"^{re.escape(dep)}([\[\s<>=!~;].*)?$", prefix) is not None


def _deps_from_setup_cfg(cfg: configparser.ConfigParser) -> list[str]:
    deps: list[str] = []
    if cfg.has_option("options", "install_requires"):
        deps.extend(cfg.get("options", "install_requires").splitlines())
    if cfg.has_section("options.extras_require"):
        for _, value in cfg.items("options.extras_require"):
            deps.extend(value.splitlines())
    return [d.strip() for d in deps if d.strip()]


def _dep_in_pyproject(dep: str, data: dict[str, Any]) -> bool:
    dep = dep.lower()
    project = data.get("project") or {}
    pep_deps = project.get("dependencies") or []
    pep_optional = project.get("optional-dependencies") or {}
    for req in pep_deps:
        if isinstance(req, str) and _pep508_match(req, dep):
            return True
    for group in pep_optional.values():
        for req in group or []:
            if isinstance(req, str) and _pep508_match(req, dep):
                return True

    tool = data.get("tool") or {}
    poetry = tool.get("poetry") or {}
    poetry_deps = poetry.get("dependencies") or {}
    if dep in {name.lower() for name in poetry_deps.keys()}:
        return True
    dev_deps = poetry.get("dev-dependencies") or {}
    if dep in {name.lower() for name in dev_deps.keys()}:
        return True
    poetry_groups = poetry.get("group") or {}
    for group in poetry_groups.values():
        group_deps = group.get("dependencies") or {}
        if dep in {name.lower() for name in group_deps.keys()}:
            return True

    pdm = tool.get("pdm") or {}
    pdm_deps = pdm.get("dependencies") or []
    for req in pdm_deps:
        if isinstance(req, str) and _pep508_match(req, dep):
            return True
    pdm_dev = pdm.get("dev-dependencies") or {}
    for group in pdm_dev.values():
        for req in group or []:
            if isinstance(req, str) and _pep508_match(req, dep):
                return True

    hatch = tool.get("hatch") or {}
    hatch_envs = hatch.get("envs") or {}
    for env in hatch_envs.values():
        deps = env.get("dependencies") or []
        for req in deps:
            if isinstance(req, str) and _pep508_match(req, dep):
                return True

    return False


def _detect_python_version(
    explicit: Optional[str],
    python_version_file: Optional[str],
    pyproject: dict[str, Any],
    setup_cfg: configparser.ConfigParser,
    setup_py: Optional[str],
    tox_ini: configparser.ConfigParser,
) -> tuple[Optional[str], Decision]:
    if explicit:
        return (
            explicit,
            Decision(
                value=explicit,
                reason="explicit_python_version provided",
                source="explicit",
            ),
        )

    if python_version_file:
        return (
            python_version_file,
            Decision(
                value=python_version_file,
                reason=".python-version present",
                source="detected",
            ),
        )

    project = pyproject.get("project") or {}
    requires_python = project.get("requires-python")
    if isinstance(requires_python, str) and requires_python.strip():
        value = requires_python.strip()
        return (
            value,
            Decision(
                value=value,
                reason="pyproject.toml requires-python present",
                source="detected",
            ),
        )

    tool = pyproject.get("tool") or {}
    poetry = tool.get("poetry") or {}
    poetry_deps = poetry.get("dependencies") or {}
    poetry_python = poetry_deps.get("python")
    if isinstance(poetry_python, str) and poetry_python.strip():
        value = poetry_python.strip()
        return (
            value,
            Decision(
                value=value,
                reason="pyproject.toml tool.poetry.dependencies.python present",
                source="detected",
            ),
        )

    pdm = tool.get("pdm") or {}
    pdm_python = pdm.get("python")
    if isinstance(pdm_python, str) and pdm_python.strip():
        value = pdm_python.strip()
        return (
            value,
            Decision(
                value=value,
                reason="pyproject.toml tool.pdm.python present",
                source="detected",
            ),
        )

    if setup_cfg.has_option("options", "python_requires"):
        cfg_value = setup_cfg.get("options", "python_requires").strip()
        if cfg_value:
            return (
                cfg_value,
                Decision(
                    value=cfg_value,
                    reason="setup.cfg python_requires present",
                    source="detected",
                ),
            )

    if setup_py:
        match = re.search(r"python_requires\s*=\s*['\"]([^'\"]+)['\"]", setup_py)
        if match:
            value = match.group(1).strip()
            return (
                value,
                Decision(
                    value=value,
                    reason="setup.py python_requires present",
                    source="detected",
                ),
            )

    for section in ("testenv", "tox"):
        if tox_ini.has_option(section, "basepython"):
            basepython = tox_ini.get(section, "basepython")
            match = re.search(r"python(\d+\.\d+)", basepython)
            if match:
                value = match.group(1)
                return (
                    value,
                    Decision(
                        value=value,
                        reason="tox.ini basepython present",
                        source="detected",
                    ),
                )

    return (
        None,
        Decision(value=None, reason="no python version metadata found", source="none"),
    )


def _detect_install_cmds(
    repo_dir: Path,
    overrides: Optional[Overrides],
    pyproject_exists: bool,
) -> tuple[list[str], Decision]:
    if overrides and overrides.install_cmds is not None:
        return (
            _normalize_cmd_list(overrides.install_cmds),
            Decision(
                value="override",
                reason="overrides.install_cmds provided",
                source="override",
                defaulted_cmd=False,
            ),
        )

    uv_lock = repo_dir / "uv.lock"
    poetry_lock = repo_dir / "poetry.lock"
    requirements = repo_dir / "requirements.txt"
    setup_py = repo_dir / "setup.py"
    setup_cfg = repo_dir / "setup.cfg"

    if pyproject_exists and uv_lock.exists():
        return (
            ["uv sync --all-extras --dev"],
            Decision(
                value="uv",
                reason="uv.lock present",
                source="detected",
                defaulted_cmd=True,
            ),
        )
    if pyproject_exists and poetry_lock.exists():
        return (
            ["poetry install"],
            Decision(
                value="poetry",
                reason="poetry.lock present",
                source="detected",
                defaulted_cmd=True,
            ),
        )
    if requirements.exists():
        return (
            ["python -m pip install -r requirements.txt"],
            Decision(
                value="pip-requirements",
                reason="requirements.txt present",
                source="detected",
                defaulted_cmd=True,
            ),
        )
    if pyproject_exists or setup_py.exists() or setup_cfg.exists():
        if pyproject_exists:
            reason = "pyproject.toml present"
        elif setup_py.exists():
            reason = "setup.py present"
        else:
            reason = "setup.cfg present"
        return (
            ["python -m pip install -e ."],
            Decision(
                value="pip-editable",
                reason=reason,
                source="detected",
                defaulted_cmd=True,
            ),
        )
    return (
        [],
        Decision(
            value=None,
            reason="no supported install files found",
            source="none",
            defaulted_cmd=None,
        ),
    )


def _detect_gate_cmd(
    name: str,
    override_cmd: Optional[str],
    detected: bool,
    detected_reason: str,
    tool_name: str,
    default_cmd: str,
) -> tuple[Optional[str], Decision]:
    if override_cmd is not None:
        cmd = override_cmd.strip()
        if cmd:
            return (
                cmd,
                Decision(
                    value="override",
                    reason=f"overrides.{name}_cmd provided",
                    source="override",
                    defaulted_cmd=False,
                ),
            )
        return (
            None,
            Decision(
                value=None,
                reason=f"overrides.{name}_cmd provided empty",
                source="override",
                defaulted_cmd=False,
            ),
        )
    if detected:
        return (
            default_cmd,
            Decision(
                value=tool_name,
                reason=detected_reason,
                source="detected",
                defaulted_cmd=True,
            ),
        )
    return (
        None,
        Decision(
            value=None,
            reason=f"no {name} signals detected",
            source="none",
            defaulted_cmd=None,
        ),
    )


def _detect_profile(request: RepoProfileRequest) -> RepoProfile:
    repo_dir = Path(request.repo_dir)
    if not repo_dir.exists():
        raise FileNotFoundError(f"Repo dir '{repo_dir}' does not exist.")
    if not repo_dir.is_dir():
        raise NotADirectoryError(f"Repo dir '{repo_dir}' is not a directory.")

    _tmp_root(repo_dir).mkdir(parents=True, exist_ok=True)

    pyproject_path = repo_dir / "pyproject.toml"
    pyproject_data = _load_toml(pyproject_path)
    pyproject_exists = pyproject_path.exists()
    python_version_file = _read_first_line(repo_dir / ".python-version")

    requirements_path = repo_dir / "requirements.txt"
    requirements_lines = (
        _read_text(requirements_path).splitlines() if requirements_path.exists() else []
    )

    setup_cfg_path = repo_dir / "setup.cfg"
    setup_cfg = _load_cfg(setup_cfg_path)
    setup_cfg_deps = _deps_from_setup_cfg(setup_cfg)

    pytest_ini_path = repo_dir / "pytest.ini"
    pytest_tool = (pyproject_data.get("tool") or {}).get("pytest") is not None
    pytest_ini_options = (pyproject_data.get("tool") or {}).get("pytest.ini_options") is not None
    pytest_config_present = (
        pytest_ini_path.exists()
        or setup_cfg.has_section("tool:pytest")
        or pytest_tool
        or pytest_ini_options
    )

    setup_py_path = repo_dir / "setup.py"
    setup_py_text = _read_text(setup_py_path) if setup_py_path.exists() else None

    tox_ini_path = repo_dir / "tox.ini"
    tox_ini = _load_cfg(tox_ini_path)

    python_version, python_decision = _detect_python_version(
        request.explicit_python_version,
        python_version_file,
        pyproject_data,
        setup_cfg,
        setup_py_text,
        tox_ini,
    )

    overrides = request.overrides
    install_cmds, install_decision = _detect_install_cmds(
        repo_dir, overrides, pyproject_exists
    )

    tests_dir = repo_dir / "tests"
    tests_root = None
    if tests_dir.exists():
        test_detected = True
        test_reason = "tests/ directory present"
        tests_root = "tests"
        test_default_cmd = "pytest -q tests"
    elif pytest_config_present:
        test_detected = True
        test_reason = "pytest config present"
        test_default_cmd = "pytest -q"
    else:
        test_detected = False
        test_reason = "no tests/ directory or pytest config"
        test_default_cmd = "pytest -q"

    ruff_config = any(
        [
            (repo_dir / "ruff.toml").exists(),
            (repo_dir / ".ruff.toml").exists(),
            (pyproject_data.get("tool") or {}).get("ruff") is not None,
        ]
    )
    dep_ruff = (
        _dep_in_pyproject("ruff", pyproject_data)
        or _requirements_have_dep(requirements_lines, "ruff")
        or _requirements_have_dep(setup_cfg_deps, "ruff")
    )
    if ruff_config:
        lint_detected = True
        lint_reason = "ruff config present"
    elif dep_ruff:
        lint_detected = True
        lint_reason = "ruff dependency present"
    else:
        lint_detected = False
        lint_reason = "no ruff config or dependency"

    mypy_config = any(
        [
            (repo_dir / "mypy.ini").exists(),
            (repo_dir / ".mypy.ini").exists(),
            setup_cfg.has_section("mypy"),
            (pyproject_data.get("tool") or {}).get("mypy") is not None,
        ]
    )
    dep_mypy = (
        _dep_in_pyproject("mypy", pyproject_data)
        or _requirements_have_dep(requirements_lines, "mypy")
        or _requirements_have_dep(setup_cfg_deps, "mypy")
    )
    if mypy_config:
        type_detected = True
        type_reason = "mypy config present"
    elif dep_mypy:
        type_detected = True
        type_reason = "mypy dependency present"
    else:
        type_detected = False
        type_reason = "no mypy config or dependency"

    test_cmd, test_decision = _detect_gate_cmd(
        "test",
        overrides.test_cmd if overrides else None,
        test_detected,
        test_reason,
        "pytest",
        test_default_cmd,
    )
    lint_cmd, lint_decision = _detect_gate_cmd(
        "lint",
        overrides.lint_cmd if overrides else None,
        lint_detected,
        lint_reason,
        "ruff",
        "ruff check .",
    )
    type_cmd, type_decision = _detect_gate_cmd(
        "type",
        overrides.type_cmd if overrides else None,
        type_detected,
        type_reason,
        "mypy",
        "mypy .",
    )

    env: dict[str, str] = {}
    if overrides and overrides.env:
        env.update(overrides.env)

    repo_setup_cmds: list[str] = []
    if overrides and overrides.repo_setup_cmds is not None:
        repo_setup_cmds = _normalize_cmd_list(overrides.repo_setup_cmds)

    idempotency_check = "warn"
    if overrides and overrides.repo_setup_idempotency_check:
        idempotency_check = overrides.repo_setup_idempotency_check
    if idempotency_check not in {"warn", "fail", "off"}:
        raise ValueError("repo_setup_idempotency_check must be warn|fail|off")

    continue_on_failure = False
    if overrides and overrides.repo_setup_continue_on_failure is not None:
        continue_on_failure = bool(overrides.repo_setup_continue_on_failure)

    allow_unsafe = False
    if overrides and overrides.repo_setup_allow_unsafe is not None:
        allow_unsafe = bool(overrides.repo_setup_allow_unsafe)

    policy_profile = overrides.policy_profile.strip() if overrides and overrides.policy_profile else None
    if policy_profile not in {None, "strict", "pragmatic"}:
        raise ValueError("policy_profile must be strict|pragmatic when provided")

    allow_editable_install = False
    if policy_profile == "pragmatic":
        allow_editable_install = True
    if overrides and overrides.allow_editable_install is not None:
        allow_editable_install = bool(overrides.allow_editable_install)

    allow_unauthenticated_apt = True
    if policy_profile == "strict":
        allow_unauthenticated_apt = False
    if overrides and overrides.allow_unauthenticated_apt is not None:
        allow_unauthenticated_apt = bool(overrides.allow_unauthenticated_apt)

    gates = GateProfile(
        test=Gate(cmd=test_cmd, timeout_sec=DEFAULT_TEST_TIMEOUT_SEC) if test_cmd else None,
        lint=Gate(cmd=lint_cmd, timeout_sec=DEFAULT_LINT_TIMEOUT_SEC) if lint_cmd else None,
        typecheck=Gate(cmd=type_cmd, timeout_sec=DEFAULT_TYPECHECK_TIMEOUT_SEC)
        if type_cmd
        else None,
    )

    detected_tools: list[str] = []
    tox_detected = tox_ini_path.exists() or (pyproject_data.get("tool") or {}).get("tox") is not None
    tox_reason = None
    if tox_ini_path.exists():
        tox_reason = "tox.ini present"
    elif (pyproject_data.get("tool") or {}).get("tox") is not None:
        tox_reason = "pyproject.toml tool.tox present"
    if tox_detected:
        detected_tools.append("tox")

    makefile_path = repo_dir / "Makefile"
    make_detected = makefile_path.exists()
    if make_detected:
        detected_tools.append("make")

    detected_tools = sorted(detected_tools)

    suggested_profiles: list[SuggestedProfile] = []
    if tox_detected:
        suggested_profiles.append(
            SuggestedProfile(
                name="tox",
                reason=tox_reason or "tox detected",
                install_cmds=["python -m pip install tox"],
                gates=GateProfile(
                    test=Gate(cmd="tox -q", timeout_sec=DEFAULT_TEST_TIMEOUT_SEC)
                ),
            )
        )
    if make_detected:
        suggested_profiles.append(
            SuggestedProfile(
                name="make",
                reason="Makefile present",
                install_cmds=[],
                gates=GateProfile(
                    test=Gate(cmd="make test", timeout_sec=DEFAULT_TEST_TIMEOUT_SEC)
                ),
            )
        )
    suggested_profiles = sorted(suggested_profiles, key=lambda profile: profile.name)

    decisions = RepoDecisions(
        python_version=python_decision,
        install=install_decision,
        tests=test_decision,
        lint=lint_decision,
        typecheck=type_decision,
    )

    missing: list[str] = []
    if not install_cmds:
        missing.append("install")
    if test_cmd is None and test_decision.source != "none":
        missing.append("tests")
    if lint_cmd is None and lint_decision.source != "none":
        missing.append("lint")
    if type_cmd is None and type_decision.source != "none":
        missing.append("typecheck")

    if "install" in missing:
        status = "unsupported"
        reason = "No supported Python install mechanism found."
    elif missing:
        status = "partial"
        reason = "Install detected but one or more gates are missing."
    else:
        status = "supported"
        reason = None

    return RepoProfile(
        status=status,
        reason=reason,
        missing=missing,
        project_root=".",
        tests_root=tests_root,
        python_version_target=python_version,
        install_cmds=install_cmds,
        repo_setup_cmds=repo_setup_cmds,
        repo_setup_idempotency_check=idempotency_check,
        repo_setup_continue_on_failure=continue_on_failure,
        repo_setup_allow_unsafe=allow_unsafe,
        allow_editable_install=allow_editable_install,
        allow_unauthenticated_apt=allow_unauthenticated_apt,
        policy_profile=policy_profile,
        gates=gates,
        decisions=decisions,
        detected_tools=detected_tools,
        suggested_profiles=suggested_profiles,
        working_dir=".",
        env=env,
    )


def _normalize_profile(profile: RepoProfile) -> dict[str, Any]:
    payload = _model_dump(profile)
    payload["working_dir"] = "."
    return payload


def _stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            tmp_path = Path(handle.name)
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _profile_id(profile: RepoProfile) -> str:
    normalized = _normalize_profile(profile)
    digest = hashlib.sha256(_stable_json(normalized).encode("utf-8")).hexdigest()
    return digest


def detect_repo_profile(request: RepoProfileRequest) -> RepoProfileResponse:
    profile = _detect_profile(request)
    profile_id = _profile_id(profile)

    repo_dir = Path(request.repo_dir)
    manifest_dir = repo_dir / ".pf_manifest"
    profile_path = manifest_dir / "repo_profile.json"
    profile_runtime = RepoProfileRuntime(
        working_dir_abs=str(repo_dir.resolve()),
        repo_dir_abs=str(repo_dir.resolve()),
    )

    payload = {
        "profile_id": profile_id,
        "profile": _model_dump(profile),
        "profile_runtime": _model_dump(profile_runtime),
    }
    _atomic_write_json(profile_path, payload)

    return RepoProfileResponse(
        profile_id=profile_id,
        profile=profile,
        profile_runtime=profile_runtime,
        profile_path=str(profile_path),
    )


if __name__ == "__main__":
    raw = json.loads(sys.stdin.read())
    req = RepoProfileRequest(**raw)
    resp = detect_repo_profile(req)
    print(json.dumps(_model_dump(resp), indent=2))
