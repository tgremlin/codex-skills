from __future__ import annotations

import json
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TIME_FORMAT = "%Y%m%dT%H%M%SZ"


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def utc_timestamp() -> str:
    return datetime.now(tz=timezone.utc).strftime(TIME_FORMAT)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copy_or_replace_dir(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst, ignore_errors=True)
        else:
            try:
                dst.unlink()
            except FileNotFoundError:
                pass
    shutil.copytree(src, dst)


@dataclass
class SkillRun:
    skill: str
    workspace_root: Path
    artifact_dir_name: str | None = None
    started_at: str = field(default_factory=utc_now_iso)
    notes: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.workspace_root = self.workspace_root.resolve()
        dir_name = self.artifact_dir_name or self.skill
        self.timestamp = utc_timestamp()
        self.run_dir = self.workspace_root / "artifacts" / dir_name / self.timestamp
        self.latest_dir = self.workspace_root / "artifacts" / dir_name / "latest"
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def record_artifact(self, path: Path) -> None:
        self.artifacts.append(str(path.relative_to(self.workspace_root)))

    def add_note(self, note: str) -> None:
        self.notes.append(note)

    def _resolve_repo_commit(self) -> str:
        result = run_command(["git", "rev-parse", "HEAD"], cwd=self.workspace_root, timeout_sec=10)
        if result.exit_code == 0 and result.stdout.strip():
            return result.stdout.strip()
        return "unknown"

    def _resolve_node_version(self) -> str | None:
        result = run_command(["node", "--version"], cwd=self.workspace_root, timeout_sec=10)
        if result.exit_code == 0:
            return result.stdout.strip()
        return None

    def finalize(
        self,
        status: str,
        *,
        emit_json: bool = False,
        provenance: dict[str, Any] | None = None,
        summary_updates: dict[str, Any] | None = None,
    ) -> int:
        ended_at = utc_now_iso()
        summary_path = self.run_dir / "summary.json"
        artifacts = sorted(set(self.artifacts + [str(summary_path.relative_to(self.workspace_root))]))
        resolved_provenance: dict[str, Any] = {
            "node_version": self._resolve_node_version(),
            "python_version": platform.python_version(),
            "repo_commit": self._resolve_repo_commit(),
            "template_id": None,
            "template_version": None,
        }
        if provenance:
            resolved_provenance.update(provenance)

        summary = {
            "artifacts": artifacts,
            "ended_at": ended_at,
            "notes": self.notes,
            **resolved_provenance,
            "skill": self.skill,
            "started_at": self.started_at,
            "status": status,
        }
        if summary_updates:
            summary.update(summary_updates)
        write_json(summary_path, summary)
        copy_or_replace_dir(self.run_dir, self.latest_dir)
        if emit_json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if status == "pass" else 1


@dataclass
class CommandResult:
    cmd: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str


def run_command(cmd: list[str], cwd: Path, timeout_sec: int = 120) -> CommandResult:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    return CommandResult(
        cmd=cmd,
        cwd=str(cwd),
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
