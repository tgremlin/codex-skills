from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    from pydantic import BaseModel, Field, ConfigDict
except Exception:  # pragma: no cover - pydantic v1
    from pydantic import BaseModel, Field
    ConfigDict = None

try:
    import docker
    from docker.errors import DockerException, ImageNotFound
except Exception:  # pragma: no cover - docker not installed
    docker = None
    DockerException = ImageNotFound = Exception


DEFAULT_MAX_LOG_BYTES = 200_000
DEFAULT_REPEATS = 1
DEFAULT_TMP_DIR_NAME = ".tmp-test"
DEFAULT_IDEMPOTENCY_CHECK = "warn"
DEFAULT_CONTINUE_ON_SETUP_FAILURE = False
DEFAULT_ALLOW_UNSAFE_SETUP = False
DEFAULT_PATH_LIST_CAP = 50

NOISE_DIRS = {
    ".pf_manifest",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    ".tmp-test",
}
NOISE_SUFFIXES = {".pyc"}
TRUNCATION_MARKER = b"\n...<truncated>...\n"


class GatesRunRequest(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    repo_dir: str = Field(..., description="Repository directory to mount")
    image_tag: str = Field(..., description="Docker image tag to run")
    profile_path: str = Field(..., description="Path to repo_profile.json")
    gates_to_run: Optional[list[str]] = Field(
        None, description="Subset of gates to run (test/lint/typecheck)"
    )
    repeats: int = Field(DEFAULT_REPEATS, description="Number of repeat runs for flake detection")
    max_log_bytes: int = Field(DEFAULT_MAX_LOG_BYTES, description="Max bytes for truncated logs")


class GateSignal(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    tool: str
    path: str
    line: int
    message: str


class GateResult(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    gate: str
    cmd: Optional[str]
    status: str
    exit_code: Optional[int]
    duration_sec: float
    signals: list[GateSignal]
    log_path: str
    log_truncated: bool
    log_excerpt: str
    pre_cmds: Optional[list[str]] = None
    import_probe: Optional[dict[str, Any]] = None
    log_flags: Optional[list[str]] = None


class GateRun(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    run_index: int
    results: list[GateResult]


class GateSummary(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    pass_count: int
    fail_count: int
    error_count: int
    timeout_count: int
    skipped_count: int
    total_count: int


class RepoSetupReport(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    cmds: list[str]
    cmds_hash: str
    status: str
    exit_code: Optional[int]
    failed_cmd_index: Optional[int] = None
    log_path: str
    log_truncated: bool
    log_excerpt: str
    idempotency_check: str
    idempotent: Optional[bool] = None
    tree_hash_before: Optional[str] = None
    tree_hash_after: Optional[str] = None
    tree_hash_after_idempotency: Optional[str] = None
    changed_baseline: Optional[bool] = None
    changed_second_pass: Optional[bool] = None
    diff_before: Optional[dict[str, Any]] = None
    diff_after: Optional[dict[str, Any]] = None
    created_files_count: Optional[int] = None
    modified_files_count: Optional[int] = None
    removed_files_count: Optional[int] = None
    created_files_paths: Optional[list[str]] = None
    modified_files_paths: Optional[list[str]] = None
    removed_files_paths: Optional[list[str]] = None
    editable_install_probe_status: Optional[str] = None
    editable_install_probe_error: Optional[str] = None
    editable_install_probe_output: Optional[str] = None
    editable_install_probe_python: Optional[str] = None
    editable_install_probe_pip: Optional[str] = None
    editable_install_probe_sys_path: Optional[list[str]] = None
    editable_install_probe_imported: Optional[str] = None
    editable_install_probe_candidates: Optional[list[str]] = None


class GateReport(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    run_id: str
    profile_id: str
    image_tag: str
    repo_dir: str
    gates_requested: list[str]
    repeats: int
    is_flaky: bool
    repo_setup: Optional[RepoSetupReport] = None
    policy_overrides: list[str] = []
    triage: Optional[dict[str, Any]] = None
    runs: list[GateRun]
    summary: GateSummary


class GatesRunResponse(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    gate_report_path: str
    summary: GateSummary
    is_flaky: bool
    run_id: str


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def _tmp_root(repo_dir: Path) -> Path:
    env_dir = os.environ.get("PF_TMP_DIR")
    if env_dir:
        return Path(env_dir)
    return repo_dir / DEFAULT_TMP_DIR_NAME


def _normalize_path(path: str) -> str:
    if path.startswith("/workspace/"):
        return path[len("/workspace/") :]
    if path.startswith("./"):
        return path[2:]
    return path


def _truncate_log(data: bytes, max_bytes: int) -> tuple[str, bool]:
    if max_bytes <= 0:
        return "", True
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="ignore"), False

    marker = TRUNCATION_MARKER
    if max_bytes <= len(marker) + 2:
        return data[:max_bytes].decode("utf-8", errors="ignore"), True

    head_size = max_bytes // 2
    tail_size = max_bytes - head_size - len(marker)
    head = data[:head_size]
    tail = data[-tail_size:]
    truncated = head + marker + tail
    return truncated.decode("utf-8", errors="ignore"), True


def _hash_cmds(cmds: list[str]) -> str:
    payload = json.dumps(cmds, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _git_summary(repo_dir: Path, cap: int = DEFAULT_PATH_LIST_CAP) -> dict[str, Any]:
    git_dir = repo_dir / ".git"
    if not git_dir.exists():
        return {"supported": False}
    try:
        status_raw = subprocess.run(
            ["git", "-C", str(repo_dir), "status", "--porcelain"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        diff_stat = subprocess.run(
            ["git", "-C", str(repo_dir), "diff", "--stat"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        status_lines = status_raw.splitlines() if status_raw else []
        untracked = [line[3:] for line in status_lines if line.startswith("?? ")]
        untracked_sorted = sorted(untracked)
        truncated = len(untracked_sorted) > cap
        return {
            "supported": True,
            "status": status_raw,
            "status_count": len(status_lines),
            "diff_stat": diff_stat,
            "untracked_count": len(untracked_sorted),
            "untracked_paths": untracked_sorted[:cap],
            "untracked_truncated": truncated,
        }
    except Exception as exc:
        return {"supported": False, "error": str(exc)}


def _git_change_counts(status_raw: str, cap: int = DEFAULT_PATH_LIST_CAP) -> dict[str, Any]:
    created: list[str] = []
    modified: list[str] = []
    removed: list[str] = []
    for line in status_raw.splitlines():
        if not line:
            continue
        if line.startswith("?? "):
            created.append(line[3:])
            continue
        code = line[:2]
        path = line[3:] if len(line) > 3 else ""
        if "D" in code:
            removed.append(path)
        elif "M" in code or "A" in code or "R" in code or "C" in code:
            modified.append(path)
    created_sorted = sorted(set(created))
    modified_sorted = sorted(set(modified))
    removed_sorted = sorted(set(removed))
    return {
        "created_count": len(created_sorted),
        "modified_count": len(modified_sorted),
        "removed_count": len(removed_sorted),
        "created_paths": created_sorted[:cap],
        "modified_paths": modified_sorted[:cap],
        "removed_paths": removed_sorted[:cap],
    }


def _is_noise_path(path: Path) -> bool:
    for part in path.parts:
        if part in NOISE_DIRS:
            return True
        if part == ".git":
            return True
    if path.suffix in NOISE_SUFFIXES:
        return True
    return False


def _iter_repo_files(repo_dir: Path, use_git: bool) -> list[Path]:
    if use_git:
        try:
            output = subprocess.run(
                ["git", "-C", str(repo_dir), "ls-files", "-z"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout
            if not output:
                return []
            parts = output.split(b"\x00")
            files = [repo_dir / part.decode("utf-8") for part in parts if part]
            return files
        except Exception:
            return []

    files: list[Path] = []
    for root, dirs, filenames in os.walk(repo_dir):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if not _is_noise_path(root_path / d)]
        for name in filenames:
            path = root_path / name
            if _is_noise_path(path):
                continue
            files.append(path)
    return files


def _tree_snapshot(repo_dir: Path, use_git: bool) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    files = _iter_repo_files(repo_dir, use_git)
    for path in files:
        try:
            rel = str(path.relative_to(repo_dir))
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            snapshot[rel] = {"hash": digest, "size": len(data)}
        except Exception:
            continue
    return snapshot


def _tree_hash(snapshot: dict[str, dict[str, Any]]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _diff_summary(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    cap: int = DEFAULT_PATH_LIST_CAP,
) -> dict[str, Any]:
    before_keys = set(before.keys())
    after_keys = set(after.keys())
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    modified = sorted(
        key for key in before_keys & after_keys if before[key]["hash"] != after[key]["hash"]
    )
    paths = added + removed + modified
    truncated = len(paths) > cap
    bytes_changed = 0
    for key in added:
        bytes_changed += after[key]["size"]
    for key in modified:
        bytes_changed += after[key]["size"]
    return {
        "supported": True,
        "added": len(added),
        "removed": len(removed),
        "modified": len(modified),
        "paths": paths[:cap],
        "paths_truncated": truncated,
        "bytes_changed": bytes_changed,
    }


def _unsafe_cmd(cmd: str) -> Optional[str]:
    patterns = [
        r"\bsudo\b",
        r"rm\s+-rf\s+/",
        r"\bmkfs\b",
        r"\bdd\s+if=",
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;:",
    ]
    for pattern in patterns:
        if re.search(pattern, cmd):
            return pattern
    return None


def _docker_available() -> bool:
    if docker is None:
        return False
    try:
        client = docker.from_env()
        client.ping()
        return True
    except DockerException:
        return False


def _get_docker_client():
    if docker is None:
        raise RuntimeError("docker SDK for Python is not installed.")
    try:
        client = docker.from_env()
        client.ping()
        return client
    except DockerException as exc:
        raise RuntimeError("Docker is not available.") from exc


def _select_gates(profile: dict[str, Any], gates_to_run: Optional[list[str]]) -> list[str]:
    available = {
        "test": "test",
        "tests": "test",
        "lint": "lint",
        "typecheck": "typecheck",
        "type": "typecheck",
        "typing": "typecheck",
    }
    if not gates_to_run:
        return ["test", "lint", "typecheck"]
    requested: list[str] = []
    for gate in gates_to_run:
        key = str(gate).strip().lower()
        mapped = available.get(key)
        if mapped and mapped not in requested:
            requested.append(mapped)
    return requested


@dataclass
class GateCommand:
    gate: str
    cmd: Optional[str]
    timeout_sec: int
    pre_cmds: list[str]


def _gate_commands(profile: dict[str, Any], gates: list[str]) -> list[GateCommand]:
    commands: list[GateCommand] = []
    gate_block = profile.get("gates") or {}
    for gate in gates:
        gate_info = gate_block.get(gate)
        if gate_info and isinstance(gate_info, dict):
            cmd = gate_info.get("cmd")
            timeout = gate_info.get("timeout_sec") or 0
        else:
            cmd = None
            timeout = 0
        commands.append(
            GateCommand(gate=gate, cmd=cmd, timeout_sec=int(timeout or 0), pre_cmds=[])
        )
    return commands


def _parse_pytest(log_text: str) -> list[GateSignal]:
    signals: list[GateSignal] = []
    patterns = [
        re.compile(r"^\s*File \"(.+?)\", line (\d+)", re.MULTILINE),
        re.compile(r"^(.+?\.py):(\d+):\s*(.+)$", re.MULTILINE),
    ]
    for pattern in patterns:
        for match in pattern.finditer(log_text):
            path = _normalize_path(match.group(1))
            line = int(match.group(2))
            if match.lastindex and match.lastindex >= 3:
                message = match.group(3)
            else:
                message = "pytest"
            signals.append(GateSignal(tool="pytest", path=path, line=line, message=message.strip()))
    return signals


def _parse_ruff(log_text: str) -> list[GateSignal]:
    signals: list[GateSignal] = []
    pattern = re.compile(r"^(.+?):(\d+):(\d+):\s*([A-Z]\d+)\s+(.*)$", re.MULTILINE)
    for match in pattern.finditer(log_text):
        path = _normalize_path(match.group(1))
        line = int(match.group(2))
        code = match.group(4)
        message = match.group(5)
        signals.append(
            GateSignal(tool="ruff", path=path, line=line, message=f"{code} {message}".strip())
        )
    return signals


def _parse_mypy(log_text: str) -> list[GateSignal]:
    signals: list[GateSignal] = []
    pattern = re.compile(r"^(.+?):(\d+):(?:\d+:)?\s*(error|note|warning):\s*(.*)$", re.MULTILINE)
    for match in pattern.finditer(log_text):
        path = _normalize_path(match.group(1))
        line = int(match.group(2))
        message = f"{match.group(3)}: {match.group(4)}"
        signals.append(GateSignal(tool="mypy", path=path, line=line, message=message.strip()))
    return signals


def _extract_signals(gate: str, cmd: Optional[str], log_text: str) -> list[GateSignal]:
    if not cmd:
        return []
    signals: list[GateSignal] = []
    if gate == "test" or "pytest" in cmd:
        signals.extend(_parse_pytest(log_text))
    if gate == "lint" or "ruff" in cmd:
        signals.extend(_parse_ruff(log_text))
    if gate == "typecheck" or "mypy" in cmd:
        signals.extend(_parse_mypy(log_text))

    seen = set()
    deduped: list[GateSignal] = []
    for signal in signals:
        key = (signal.tool, signal.path, signal.line, signal.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(signal)
    return deduped


def _run_container_gate(
    client,
    image_tag: str,
    repo_dir: Path,
    gate_cmd: GateCommand,
    env: dict[str, str],
    max_log_bytes: int,
    log_path: Path,
) -> GateResult:
    if not gate_cmd.cmd:
        return GateResult(
            gate=gate_cmd.gate,
            cmd=None,
            status="skipped",
            exit_code=None,
            duration_sec=0.0,
            signals=[],
            log_path=str(log_path),
            log_truncated=False,
            log_excerpt="",
        )

    timeout = gate_cmd.timeout_sec or 0
    start = time.time()
    status = "error"
    exit_code: Optional[int] = None
    log_bytes = b""

    container = None
    import_probe: Optional[dict[str, Any]] = None
    log_flags: list[str] = []
    cmd_parts = []
    if gate_cmd.pre_cmds:
        cmd_parts.extend(gate_cmd.pre_cmds)
    cmd_parts.append(gate_cmd.cmd)
    full_cmd = " && ".join(cmd_parts)
    try:
        container = client.containers.run(
            image_tag,
            command=["/bin/sh", "-lc", full_cmd],
            working_dir="/workspace",
            environment=env,
            volumes={str(repo_dir): {"bind": "/workspace", "mode": "rw"}},
            detach=True,
        )

        if timeout <= 0:
            result = container.wait()
            exit_code = result.get("StatusCode") if isinstance(result, dict) else None
            status = "pass" if exit_code == 0 else "fail"
        else:
            deadline = time.time() + timeout
            while time.time() < deadline:
                container.reload()
                if container.status in ("exited", "dead"):
                    result = container.wait(timeout=1)
                    exit_code = result.get("StatusCode") if isinstance(result, dict) else None
                    status = "pass" if exit_code == 0 else "fail"
                    break
                time.sleep(0.5)
            if status not in ("pass", "fail"):
                try:
                    container.kill()
                except DockerException:
                    pass
                status = "timeout"

        log_bytes = container.logs(stdout=True, stderr=True)
        lower = log_bytes.decode("utf-8", errors="ignore").lower()
        if "contextualversionconflict" in lower or "resolutionimpossible" in lower:
            log_flags.append("dependency_conflict")
        if "no matching distribution found" in lower or "could not find a version that satisfies the requirement" in lower:
            log_flags.append("invalid_requirement")
        if "pywin32" in lower and "no matching distribution found" in lower:
            log_flags.append("platform_incompatible_dependency")
        if gate_cmd.pre_cmds:
            text = log_bytes.decode("utf-8", errors="ignore")
            for line in text.splitlines():
                if line.startswith("IMPORT_PROBE_JSON="):
                    payload = line.split("=", 1)[1].strip()
                    try:
                        import_probe = json.loads(payload)
                    except Exception:
                        import_probe = {"error": "invalid_json"}
    except (DockerException, Exception):
        status = "error"
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except DockerException:
                pass

    duration = max(0.0, time.time() - start)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_bytes(log_bytes)
    log_excerpt, truncated = _truncate_log(log_bytes, max_log_bytes)
    signals = _extract_signals(gate_cmd.gate, gate_cmd.cmd, log_excerpt)

    return GateResult(
        gate=gate_cmd.gate,
        cmd=gate_cmd.cmd,
        status=status,
        exit_code=exit_code,
        duration_sec=duration,
        signals=signals,
        log_path=str(log_path),
        log_truncated=truncated,
        log_excerpt=log_excerpt,
        pre_cmds=gate_cmd.pre_cmds or None,
        import_probe=import_probe,
        log_flags=log_flags or None,
    )


def _run_repo_setup(
    client,
    image_tag: str,
    repo_dir: Path,
    repo_setup_cmds: list[str],
    env: dict[str, str],
    max_log_bytes: int,
    log_path: Path,
    idempotency_check: str,
    continue_on_failure: bool,
    allow_unsafe: bool,
    allow_editable_install: bool,
) -> RepoSetupReport:
    cmds = [cmd.strip() for cmd in repo_setup_cmds if cmd.strip()]
    use_git = (repo_dir / ".git").exists()
    snapshot_before = _tree_snapshot(repo_dir, use_git)
    tree_hash_before = _tree_hash(snapshot_before)
    diff_before = _git_summary(repo_dir) if use_git else None
    if not use_git:
        diff_before = _diff_summary(snapshot_before, snapshot_before)
    if not cmds:
        return RepoSetupReport(
            cmds=[],
            cmds_hash=_hash_cmds([]),
            status="skipped",
            exit_code=None,
            failed_cmd_index=None,
            log_path=str(log_path),
            log_truncated=False,
            log_excerpt="",
            idempotency_check=idempotency_check,
            idempotent=None,
            tree_hash_before=tree_hash_before,
            tree_hash_after=tree_hash_before,
            tree_hash_after_idempotency=tree_hash_before,
            diff_before=diff_before,
            diff_after=diff_before,
        )

    status = "error"
    exit_code: Optional[int] = None
    failed_cmd_index: Optional[int] = None
    log_bytes = b""
    probe_status: Optional[str] = None
    probe_error: Optional[str] = None
    probe_output: Optional[str] = None
    probe_python: Optional[str] = None
    probe_pip: Optional[str] = None
    probe_sys_path: Optional[list[str]] = None
    probe_imported: Optional[str] = None
    probe_candidates: Optional[list[str]] = None

    for index, cmd in enumerate(cmds):
        unsafe = _unsafe_cmd(cmd)
        if unsafe and not allow_unsafe:
            status = "fail"
            exit_code = None
            failed_cmd_index = index
            log_bytes += (
                f"[blocked] unsafe repo_setup_cmd index {index}: {cmd}\n".encode("utf-8")
            )
            break

        container = None
        try:
            log_bytes += f"[cmd {index}] {cmd}\n".encode("utf-8")
            container = client.containers.run(
                image_tag,
                command=["/bin/sh", "-lc", cmd],
                working_dir="/workspace",
                environment=env,
                volumes={str(repo_dir): {"bind": "/workspace", "mode": "rw"}},
                detach=True,
            )
            result = container.wait()
            exit_code = result.get("StatusCode") if isinstance(result, dict) else None
            log_bytes += container.logs(stdout=True, stderr=True)
            if exit_code != 0:
                status = "fail"
                failed_cmd_index = index
                break
            status = "pass"
        except (DockerException, Exception):
            status = "error"
            failed_cmd_index = index
            break
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:
                    pass

    if status == "pass" and allow_editable_install and _contains_editable_install(cmds):
        probe_candidates = _candidate_import_names(repo_dir)
        if not probe_candidates:
            probe_status = "skipped"
            probe_error = "no_candidates"
        else:
            probe_cmd = _build_import_probe_cmd(probe_candidates)
            container = None
            try:
                log_bytes += b"[import probe] running editable install import probe\n"
                container = client.containers.run(
                    image_tag,
                    command=["/bin/sh", "-lc", probe_cmd],
                    working_dir="/workspace",
                    environment=env,
                    volumes={str(repo_dir): {"bind": "/workspace", "mode": "rw"}},
                    detach=True,
                )
                result = container.wait()
                exit_code = result.get("StatusCode") if isinstance(result, dict) else None
                output = container.logs(stdout=True, stderr=True)
                log_bytes += output
                text = output.decode("utf-8", errors="ignore")
                probe_output = text.strip() if text else ""
                payload = None
                for line in reversed(text.splitlines()):
                    line = line.strip()
                    if line.startswith("{") and line.endswith("}"):
                        try:
                            payload = json.loads(line)
                            break
                        except Exception:
                            continue
                if payload:
                    probe_python = payload.get("python")
                    probe_pip = payload.get("pip")
                    probe_sys_path = payload.get("sys_path")
                    probe_imported = payload.get("imported")
                    probe_status = "pass" if probe_imported else "fail"
                else:
                    probe_status = "error"
                    probe_error = "no_json_payload"
            except (DockerException, Exception) as exc:
                probe_status = "error"
                probe_error = str(exc)
            finally:
                if container is not None:
                    try:
                        container.remove(force=True)
                    except DockerException:
                        pass

    snapshot_after = _tree_snapshot(repo_dir, use_git)
    tree_hash_after = _tree_hash(snapshot_after)
    diff_after = _git_summary(repo_dir) if use_git else _diff_summary(snapshot_before, snapshot_after)
    change_counts = None
    if use_git and diff_after and diff_after.get("supported") and diff_after.get("status") is not None:
        change_counts = _git_change_counts(diff_after.get("status") or "")
    elif diff_after and diff_after.get("supported"):
        change_counts = {
            "created_count": diff_after.get("added"),
            "modified_count": diff_after.get("modified"),
            "removed_count": diff_after.get("removed"),
            "created_paths": diff_after.get("paths"),
            "modified_paths": [],
            "removed_paths": [],
        }

    idempotent: Optional[bool] = None
    tree_hash_after_idempotency = tree_hash_after
    if status == "pass" and idempotency_check != "off":
        snapshot_before_idem = snapshot_after
        diff_after_idem = diff_after
        for index, cmd in enumerate(cmds):
            container = None
            try:
                log_bytes += f"[idempotency cmd {index}] {cmd}\n".encode("utf-8")
                container = client.containers.run(
                    image_tag,
                    command=["/bin/sh", "-lc", cmd],
                    working_dir="/workspace",
                    environment=env,
                    volumes={str(repo_dir): {"bind": "/workspace", "mode": "rw"}},
                    detach=True,
                )
                result = container.wait()
                exit_code = result.get("StatusCode") if isinstance(result, dict) else None
                log_bytes += container.logs(stdout=True, stderr=True)
                if exit_code != 0:
                    status = "fail" if idempotency_check == "fail" else status
                    failed_cmd_index = index
                    break
            except (DockerException, Exception):
                status = "error"
                failed_cmd_index = index
                break
            finally:
                if container is not None:
                    try:
                        container.remove(force=True)
                    except DockerException:
                        pass

        snapshot_after_idem = _tree_snapshot(repo_dir, use_git)
        tree_hash_after_idempotency = _tree_hash(snapshot_after_idem)
        untracked_before = (
            diff_after.get("untracked_paths", []) if use_git and diff_after else []
        )
        diff_after_idem = (
            _git_summary(repo_dir) if use_git else _diff_summary(snapshot_before_idem, snapshot_after_idem)
        )
        untracked_after = (
            diff_after_idem.get("untracked_paths", []) if use_git and diff_after_idem else []
        )
        untracked_count_before = diff_after.get("untracked_count") if use_git and diff_after else None
        untracked_count_after = diff_after_idem.get("untracked_count") if use_git and diff_after_idem else None
        idempotent = tree_hash_after_idempotency == tree_hash_after
        if use_git:
            if untracked_count_before != untracked_count_after:
                idempotent = False
            elif (
                diff_after.get("untracked_truncated") is False
                and diff_after_idem.get("untracked_truncated") is False
                and sorted(untracked_before) != sorted(untracked_after)
            ):
                idempotent = False
        if idempotent is False and idempotency_check == "fail":
            status = "fail"

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_bytes(log_bytes)
    log_excerpt, truncated = _truncate_log(log_bytes, max_log_bytes)

    changed_baseline = None
    if change_counts:
        changed_baseline = (
            (change_counts.get("created_count") or 0)
            + (change_counts.get("modified_count") or 0)
            + (change_counts.get("removed_count") or 0)
        ) > 0
    elif tree_hash_before and tree_hash_after:
        changed_baseline = tree_hash_before != tree_hash_after

    changed_second_pass = None
    if idempotent is not None:
        changed_second_pass = not idempotent

    return RepoSetupReport(
        cmds=cmds,
        cmds_hash=_hash_cmds(cmds),
        status=status,
        exit_code=exit_code,
        failed_cmd_index=failed_cmd_index,
        log_path=str(log_path),
        log_truncated=truncated,
        log_excerpt=log_excerpt,
        idempotency_check=idempotency_check,
        idempotent=idempotent,
        tree_hash_before=tree_hash_before,
        tree_hash_after=tree_hash_after,
        tree_hash_after_idempotency=tree_hash_after_idempotency,
        changed_baseline=changed_baseline,
        changed_second_pass=changed_second_pass,
        diff_before=diff_before,
        diff_after=diff_after,
        created_files_count=(change_counts or {}).get("created_count"),
        modified_files_count=(change_counts or {}).get("modified_count"),
        removed_files_count=(change_counts or {}).get("removed_count"),
        created_files_paths=(change_counts or {}).get("created_paths"),
        modified_files_paths=(change_counts or {}).get("modified_paths"),
        removed_files_paths=(change_counts or {}).get("removed_paths"),
        editable_install_probe_status=probe_status,
        editable_install_probe_error=probe_error,
        editable_install_probe_output=probe_output,
        editable_install_probe_python=probe_python,
        editable_install_probe_pip=probe_pip,
        editable_install_probe_sys_path=probe_sys_path,
        editable_install_probe_imported=probe_imported,
        editable_install_probe_candidates=probe_candidates,
    )


def _summarize(results: list[GateResult]) -> GateSummary:
    counts = {"pass": 0, "fail": 0, "error": 0, "timeout": 0, "skipped": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    total = sum(counts.values())
    return GateSummary(
        pass_count=counts.get("pass", 0),
        fail_count=counts.get("fail", 0),
        error_count=counts.get("error", 0),
        timeout_count=counts.get("timeout", 0),
        skipped_count=counts.get("skipped", 0),
        total_count=total,
    )


def _signature(results: list[GateResult]) -> str:
    payload = [(res.gate, res.status, res.exit_code) for res in results]
    normalized = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _overall_status(summary: GateSummary) -> str:
    if summary.error_count > 0:
        return "error"
    if summary.fail_count > 0:
        return "fail"
    return "pass"


def _read_image_manifest(repo_dir: Path) -> dict[str, Any]:
    manifest_path = repo_dir / ".pf_manifest" / "image_build" / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _classify_failure(
    status: str,
    log_excerpt: str,
) -> tuple[str, str, str]:
    text = log_excerpt or ""
    lower = text.lower()
    if status == "timeout":
        return "environment_failure", "timeout", "infra_issue"
    if "contextualversionconflict" in lower or "resolutionimpossible" in lower:
        return "setup_failure", "dependency_conflict", "infra_issue"
    if "dependency conflict" in lower or "versionconflict" in lower:
        return "setup_failure", "dependency_conflict", "infra_issue"
    if "command not found" in lower or "no such file or directory" in lower:
        return "harness_failure", "runner_missing", "infra_issue"
    if "modulenotfounderror" in lower or "no module named" in lower:
        return "environment_failure", "module_not_found", "infra_issue"
    if "importerror while importing test module" in lower or "error collecting" in lower:
        return "harness_failure", "collection_error", "dataset_metadata_issue"
    if "assertionerror" in lower or "failed (failures" in lower or "fail:" in lower:
        return "test_failure", "assertion_failure", "actionable_bug"
    if status == "fail":
        return "test_failure", "test_command_failed", "actionable_bug"
    if status == "error":
        return "environment_failure", "test_command_failed", "infra_issue"
    return "environment_failure", "runtime_error", "infra_issue"


def _extract_exception_type(log_excerpt: str) -> Optional[str]:
    if not log_excerpt:
        return None
    for line in reversed(log_excerpt.splitlines()):
        if ":" not in line:
            continue
        left = line.split(":", 1)[0].strip()
        if left.endswith("Error") or left.endswith("Exception"):
            return left
    return None


def _extract_first_error_line(log_excerpt: str) -> Optional[str]:
    if not log_excerpt:
        return None
    for line in log_excerpt.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("E   "):
            return stripped[4:].strip()
        if "Error:" in stripped or "Exception:" in stripped:
            return stripped
    return None


def _contains_editable_install(cmds: list[str]) -> bool:
    for cmd in cmds:
        normalized = " ".join(cmd.split())
        if "pip install -e ." in normalized:
            return True
    return False


def _valid_import_name(name: str) -> bool:
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(ch.isalnum() or ch == "_" for ch in name)


def _candidate_import_names(repo_dir: Path) -> list[str]:
    candidates: set[str] = set()
    repo_name = repo_dir.name.replace("-", "_")
    if _valid_import_name(repo_name):
        candidates.add(repo_name)
    for base in [repo_dir, repo_dir / "src"]:
        if not base.exists():
            continue
        for entry in base.iterdir():
            if entry.name.startswith("."):
                continue
            if entry.is_dir() and (entry / "__init__.py").exists():
                if _valid_import_name(entry.name):
                    candidates.add(entry.name)
            if entry.is_file() and entry.suffix == ".py":
                if entry.name in {"setup.py", "conftest.py", "__init__.py"}:
                    continue
                if _valid_import_name(entry.stem):
                    candidates.add(entry.stem)
    ordered = sorted(candidates)
    return ordered[:10]


def _build_import_probe_cmd(candidates: list[str]) -> str:
    payload = json.dumps(candidates)
    code = "\n".join(
        [
            "import importlib, json, subprocess, sys",
            f"candidates = json.loads({payload!r})",
            "results = {}",
            "first = None",
            "for name in candidates:",
            "    try:",
            "        mod = importlib.import_module(name)",
            "        results[name] = getattr(mod, '__file__', 'ok')",
            "        if first is None:",
            "            first = name",
            "    except Exception as exc:",
            "        results[name] = f\"{type(exc).__name__}: {exc}\"",
            "pip_version = ''",
            "try:",
            "    pip_version = subprocess.check_output([sys.executable, '-m', 'pip', '--version']).decode().strip()",
            "except Exception as exc:",
            "    pip_version = f\"error: {exc}\"",
            "payload = {",
            "    'python': sys.executable,",
            "    'pip': pip_version,",
            "    'sys_path': sys.path[:5],",
            "    'candidates': candidates,",
            "    'imported': first,",
            "    'results': results,",
            "}",
            "print('IMPORT_PROBE_JSON=' + json.dumps(payload))",
        ]
    )
    return f"python -c {shlex.quote(code)}"


def _parse_dataset_info(repo_dir: Path) -> dict[str, Optional[str]]:
    parts = repo_dir.parts
    if "bugsinpy" in parts:
        idx = parts.index("bugsinpy")
        if len(parts) > idx + 3:
            return {
                "dataset": "bugsinpy",
                "project": parts[idx + 1],
                "bug_id": parts[idx + 2],
                "variant": parts[idx + 3],
            }
    return {"dataset": None, "project": None, "bug_id": None, "variant": None}


def _unsupported_registry_path(repo_dir: Path) -> Path:
    info = _parse_dataset_info(repo_dir)
    if info.get("dataset") == "bugsinpy":
        root = Path(*repo_dir.parts[: repo_dir.parts.index("bugsinpy") + 1])
        return root / "unsupported_registry.json"
    return repo_dir / ".pf_manifest" / "unsupported_registry.json"


def _update_unsupported_registry(
    repo_dir: Path,
    triage: dict[str, Any],
    profile: dict[str, Any],
    report_path: Path,
) -> None:
    if triage.get("actionability") not in {"dataset_metadata_issue", "infra_issue"}:
        return

    info = _parse_dataset_info(repo_dir)
    stage = triage.get("stage") or "gates_run"
    key = {
        "dataset": info.get("dataset") or "unknown",
        "project": info.get("project") or repo_dir.name,
        "bug_id": info.get("bug_id"),
        "variant": info.get("variant"),
        "failure_class": triage.get("failure_class"),
        "failure_reason": triage.get("failure_reason"),
        "python_version": profile.get("python_version_target"),
        "registry_bucket": triage.get("registry_bucket"),
        "stage": stage,
        "policy_profile": triage.get("policy_profile"),
    }
    registry_path = _unsupported_registry_path(repo_dir)
    registry = {"entries": []}
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception:
            registry = {"entries": []}
    entries = registry.get("entries") or []

    def _matches(entry: dict[str, Any]) -> bool:
        for k, v in key.items():
            if k == "registry_bucket":
                entry_bucket = entry.get(k) or "dataset_metadata_issue"
                if entry_bucket != v:
                    return False
                continue
            if k == "stage":
                entry_stage = entry.get(k) or "gates_run"
                if entry_stage != v:
                    return False
                continue
            if entry.get(k) != v:
                return False
        return True

    existing = next((entry for entry in entries if _matches(entry)), None)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    suggested = triage.get("suggested_remediation")
    retry_on_policy_change = bool(triage.get("retry_on_policy_change"))
    if existing:
        existing["last_seen_at"] = now
        existing["count"] = int(existing.get("count", 1)) + 1
        existing["report_path"] = str(report_path)
        if suggested:
            existing["suggested_remediation"] = suggested
        existing["retry_on_policy_change"] = retry_on_policy_change
    else:
        entries.append(
            {
                **key,
                "first_seen_at": now,
                "last_seen_at": now,
                "count": 1,
                "report_path": str(report_path),
                "suggested_remediation": suggested,
                "retry_on_policy_change": retry_on_policy_change,
            }
        )
    registry["entries"] = entries
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(registry_path, json.dumps(registry, indent=2))


def _triage_summary(
    summary: GateSummary,
    runs: list[GateRun],
    repo_setup: Optional[RepoSetupReport],
    manifest: dict[str, Any],
    policy_overrides: list[str],
    repo_dir: Path,
    profile: dict[str, Any],
    allow_editable_install: bool,
    editable_install_location: Optional[str],
    import_probe_context: Optional[str],
) -> dict[str, Any]:
    overall = _overall_status(summary)
    failure_class = None
    failure_reason = None
    actionability = None
    unsupported_reason = None
    registry_bucket = None
    retry_on_policy_change = False

    if repo_setup and repo_setup.status in {"fail", "error"}:
        failure_class = "setup_failure"
        reason = "setup_failed"
        log = repo_setup.log_excerpt or ""
        if "[blocked] unsafe" in log:
            reason = "unsafe_setup_cmd"
        elif "contextualversionconflict" in log.lower() or "resolutionimpossible" in log.lower():
            reason = "dependency_conflict"
        elif "dependency conflict" in log.lower() or "versionconflict" in log.lower():
            reason = "dependency_conflict"
        elif "no matching distribution found" in log.lower():
            reason = "invalid_setup_cmd"
        failure_reason = reason
        if reason == "dependency_conflict":
            actionability = "infra_issue"
        else:
            actionability = "dataset_metadata_issue"
        unsupported_reason = reason
    else:
        last_results: list[GateResult] = []
        if runs:
            last_results = runs[-1].results
        failing = next(
            (res for res in last_results if res.status in {"fail", "error"}), None
        )
        if failing:
            failure_class, failure_reason, actionability = _classify_failure(
                failing.status, failing.log_excerpt
            )
            if failing.log_flags:
                if "dependency_conflict" in failing.log_flags:
                    failure_class = "setup_failure"
                    failure_reason = "dependency_conflict"
                    actionability = "infra_issue"
            if failure_reason == "module_not_found":
                unsupported_reason = "missing_install_step"
    if actionability in {"dataset_metadata_issue", "infra_issue"}:
        registry_bucket = actionability
        if actionability == "infra_issue" and unsupported_reason == "missing_install_step":
            retry_on_policy_change = True
    suggested_repo_setup = None
    suggested_reason = None
    if (
        allow_editable_install
        and failure_reason == "module_not_found"
        and (
            (repo_dir / "setup.py").exists()
            or (repo_dir / "pyproject.toml").exists()
            or (repo_dir / "setup.cfg").exists()
        )
    ):
        suggested_repo_setup = ["python -m pip install -e ."]
        suggested_reason = "missing_install_step"

    probe_payload = None
    probe_python = None
    probe_pip = None
    if runs:
        last_results = runs[-1].results
        for result in last_results:
            if result.gate == "test" and result.import_probe:
                probe_payload = result.import_probe
                if isinstance(probe_payload, dict):
                    probe_python = probe_payload.get("python")
                    probe_pip = probe_payload.get("pip")
                break

    repo_setup_changed = None
    if repo_setup:
        if repo_setup.changed_baseline is not None:
            repo_setup_changed = repo_setup.changed_baseline
        elif repo_setup.tree_hash_before and repo_setup.tree_hash_after:
            repo_setup_changed = repo_setup.tree_hash_before != repo_setup.tree_hash_after

    root_exception_type = None
    first_error_line = None
    if runs:
        last_results = runs[-1].results
        failing = next((res for res in last_results if res.status in {"fail", "error"}), None)
        if failing:
            root_exception_type = _extract_exception_type(failing.log_excerpt)
            first_error_line = _extract_first_error_line(failing.log_excerpt)

    triage = {
        "triage_schema_version": 1,
        "stage": "gates_run",
        "status": overall,
        "failure_class": failure_class,
        "failure_reason": failure_reason,
        "root_exception_type": root_exception_type,
        "first_error_line": first_error_line,
        "actionability": actionability,
        "unsupported_reason": unsupported_reason,
        "registry_bucket": registry_bucket,
        "retry_on_policy_change": retry_on_policy_change,
        "policy_overrides": policy_overrides,
        "runner_env": sys.executable,
        "policy_profile": profile.get("policy_profile"),
        "apt_security_mode": manifest.get("apt_security_mode"),
        "apt_fallback_used": manifest.get("apt_security_mode") in {"archive", "archive_unauthenticated"},
        "security_risk_flags": [
            "unauthenticated_apt"
            for _ in [1]
            if manifest.get("apt_security_mode") == "archive_unauthenticated"
        ],
        "base_image_tag": manifest.get("base_image_tag"),
        "base_image_digest": manifest.get("base_image_digest"),
        "repo_setup_status": repo_setup.status if repo_setup else None,
        "repo_setup_changed_baseline": repo_setup_changed,
        "repo_setup_changed_second_pass": (
            None if repo_setup is None else repo_setup.changed_second_pass
        ),
        "repo_setup_created_files_count": repo_setup.created_files_count if repo_setup else None,
        "repo_setup_modified_files_count": repo_setup.modified_files_count if repo_setup else None,
        "repo_setup_removed_files_count": repo_setup.removed_files_count if repo_setup else None,
        "editable_install_probe_status": repo_setup.editable_install_probe_status
        if repo_setup
        else None,
        "editable_install_probe_error": repo_setup.editable_install_probe_error
        if repo_setup
        else None,
        "editable_install_probe_python": repo_setup.editable_install_probe_python
        if repo_setup
        else None,
        "editable_install_probe_pip": repo_setup.editable_install_probe_pip if repo_setup else None,
        "editable_install_probe_sys_path": repo_setup.editable_install_probe_sys_path
        if repo_setup
        else None,
        "editable_install_probe_imported": repo_setup.editable_install_probe_imported
        if repo_setup
        else None,
        "editable_install_location": editable_install_location,
        "import_probe_context": import_probe_context,
        "gate_import_probe": probe_payload,
        "gate_python": probe_python,
        "gate_pip": probe_pip,
        "suggested_repo_setup_cmds": suggested_repo_setup,
        "suggested_remediation": suggested_reason,
    }
    return triage


def run_gates(request: GatesRunRequest) -> GatesRunResponse:
    repo_dir = Path(request.repo_dir).resolve()
    if not repo_dir.exists():
        raise FileNotFoundError(f"Repo dir '{repo_dir}' does not exist.")
    if not repo_dir.is_dir():
        raise NotADirectoryError(f"Repo dir '{repo_dir}' is not a directory.")

    _tmp_root(repo_dir).mkdir(parents=True, exist_ok=True)

    profile_path = Path(request.profile_path)
    if not profile_path.exists():
        raise FileNotFoundError(f"Profile path '{profile_path}' does not exist.")

    payload = _read_json(profile_path)
    profile = payload.get("profile") or payload
    profile_id = payload.get("profile_id") or "unknown"

    client = _get_docker_client()
    try:
        client.images.get(request.image_tag)
    except ImageNotFound as exc:
        raise RuntimeError(f"Image '{request.image_tag}' not found.") from exc

    gates = _select_gates(profile, request.gates_to_run)
    gate_commands = _gate_commands(profile, gates)

    env = dict(profile.get("env") or {})
    env.setdefault("PF_TMP_DIR", "/workspace/.tmp-test")

    repeats = max(1, int(request.repeats or DEFAULT_REPEATS))
    max_log_bytes = int(request.max_log_bytes or DEFAULT_MAX_LOG_BYTES)
    allow_editable_install = bool(profile.get("allow_editable_install", False))
    editable_install_location = None
    import_probe_context = None

    if allow_editable_install:
        has_packaging = (
            (repo_dir / "setup.py").exists()
            or (repo_dir / "setup.cfg").exists()
            or (repo_dir / "pyproject.toml").exists()
        )
        if has_packaging:
            pre_cmds = ["python -m pip install -e ."]
            candidates = _candidate_import_names(repo_dir)
            if candidates:
                pre_cmds.append(_build_import_probe_cmd(candidates))
                import_probe_context = "gate_pre"
            editable_install_location = "gate_container"
            for cmd in gate_commands:
                if cmd.gate == "test" and cmd.cmd:
                    cmd.pre_cmds = list(pre_cmds)

    run_id = uuid.uuid4().hex
    gates_dir = repo_dir / ".pf_manifest" / "gates"
    gates_dir.mkdir(parents=True, exist_ok=True)

    repo_setup_cmds = profile.get("repo_setup_cmds") or []
    idempotency_check = profile.get("repo_setup_idempotency_check") or DEFAULT_IDEMPOTENCY_CHECK
    if idempotency_check not in {"warn", "fail", "off"}:
        raise ValueError("repo_setup_idempotency_check must be warn|fail|off")
    continue_on_failure = bool(
        profile.get("repo_setup_continue_on_failure", DEFAULT_CONTINUE_ON_SETUP_FAILURE)
    )
    allow_unsafe = bool(profile.get("repo_setup_allow_unsafe", DEFAULT_ALLOW_UNSAFE_SETUP))

    policy_overrides: list[str] = []
    if idempotency_check != DEFAULT_IDEMPOTENCY_CHECK:
        policy_overrides.append(f"repo_setup_idempotency_{idempotency_check}")
    if continue_on_failure != DEFAULT_CONTINUE_ON_SETUP_FAILURE:
        policy_overrides.append("repo_setup_continue_on_failure")
    if allow_unsafe != DEFAULT_ALLOW_UNSAFE_SETUP:
        policy_overrides.append("repo_setup_allow_unsafe")
    if allow_editable_install:
        policy_overrides.append("allow_editable_install")
    repo_setup_report = None
    if repo_setup_cmds:
        setup_log_path = gates_dir / f"{run_id}.setup.log"
        repo_setup_report = _run_repo_setup(
            client,
            request.image_tag,
            repo_dir,
            repo_setup_cmds,
            env,
            max_log_bytes,
            setup_log_path,
            idempotency_check,
            continue_on_failure,
            allow_unsafe,
            allow_editable_install,
        )

    runs: list[GateRun] = []
    signatures: list[str] = []

    if (
        repo_setup_report
        and repo_setup_report.status in {"fail", "error"}
        and not continue_on_failure
    ):
        results: list[GateResult] = []
        for gate_cmd in gate_commands:
            results.append(
                GateResult(
                    gate=gate_cmd.gate,
                    cmd=gate_cmd.cmd,
                    status="error",
                    exit_code=None,
                    duration_sec=0.0,
                    signals=[],
                    log_path=repo_setup_report.log_path,
                    log_truncated=repo_setup_report.log_truncated,
                    log_excerpt="repo_setup failed; see setup log for details",
                )
            )
        runs.append(GateRun(run_index=0, results=results))
    else:
        for run_index in range(repeats):
            results: list[GateResult] = []
            for gate_cmd in gate_commands:
                log_path = gates_dir / f"{run_id}.run{run_index}.{gate_cmd.gate}.log"
                result = _run_container_gate(
                    client,
                    request.image_tag,
                    repo_dir,
                    gate_cmd,
                    env,
                    max_log_bytes,
                    log_path,
                )
                results.append(result)
            runs.append(GateRun(run_index=run_index, results=results))
            signatures.append(_signature(results))

    is_flaky = len(set(signatures)) > 1
    summary = _summarize(runs[-1].results) if runs else _summarize([])

    manifest = _read_image_manifest(repo_dir)
    triage = _triage_summary(
        summary,
        runs,
        repo_setup_report,
        manifest,
        policy_overrides,
        repo_dir,
        profile,
        allow_editable_install,
        editable_install_location,
        import_probe_context,
    )

    report = GateReport(
        run_id=run_id,
        profile_id=profile_id,
        image_tag=request.image_tag,
        repo_dir=str(repo_dir),
        gates_requested=gates,
        repeats=repeats,
        is_flaky=is_flaky,
        repo_setup=repo_setup_report,
        policy_overrides=policy_overrides,
        triage=triage,
        runs=runs,
        summary=summary,
    )

    report_path = gates_dir / f"{run_id}.json"
    _atomic_write_text(report_path, json.dumps(_model_dump(report), indent=2))
    _update_unsupported_registry(repo_dir, triage, profile, report_path)

    return GatesRunResponse(
        gate_report_path=str(report_path),
        summary=summary,
        is_flaky=is_flaky,
        run_id=run_id,
    )


if __name__ == "__main__":
    raw = json.loads(sys.stdin.read())
    req = GatesRunRequest(**raw)
    resp = run_gates(req)
    print(json.dumps(_model_dump(resp), indent=2))
