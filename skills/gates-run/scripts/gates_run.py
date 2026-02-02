from __future__ import annotations

import hashlib
import json
import os
import re
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
    available = ["test", "lint", "typecheck"]
    if not gates_to_run:
        return available
    requested = [gate for gate in gates_to_run if gate in available]
    return requested


@dataclass
class GateCommand:
    gate: str
    cmd: Optional[str]
    timeout_sec: int


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
        commands.append(GateCommand(gate=gate, cmd=cmd, timeout_sec=int(timeout or 0)))
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
    try:
        container = client.containers.run(
            image_tag,
            command=["/bin/sh", "-lc", gate_cmd.cmd],
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

    run_id = uuid.uuid4().hex
    gates_dir = repo_dir / ".pf_manifest" / "gates"
    gates_dir.mkdir(parents=True, exist_ok=True)

    runs: list[GateRun] = []
    signatures: list[str] = []

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

    report = GateReport(
        run_id=run_id,
        profile_id=profile_id,
        image_tag=request.image_tag,
        repo_dir=str(repo_dir),
        gates_requested=gates,
        repeats=repeats,
        is_flaky=is_flaky,
        runs=runs,
        summary=summary,
    )

    report_path = gates_dir / f"{run_id}.json"
    _atomic_write_text(report_path, json.dumps(_model_dump(report), indent=2))

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
