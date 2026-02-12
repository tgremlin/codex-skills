from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from swarm_skills.runtime import SkillRun, run_command, write_json


CHECKS = [
    ["python3", "--version"],
    ["node", "--version"],
    ["npm", "--version"],
    ["pytest", "--version"],
]


def _resolve_command(cmd: list[str], workspace_root: Path) -> list[str] | None:
    tool = cmd[0]
    if shutil.which(tool):
        return cmd

    venv_tool = workspace_root / ".venv" / "bin" / tool
    if venv_tool.exists() and venv_tool.is_file():
        return [str(venv_tool), *cmd[1:]]
    return None


def run(args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    skill_run = SkillRun(skill="doctor", workspace_root=workspace_root)

    results: list[dict[str, Any]] = []
    failures = 0
    for cmd in CHECKS:
        resolved = _resolve_command(cmd, workspace_root)
        if resolved is None:
            tool = cmd[0]
            failures += 1
            results.append(
                {
                    "available": False,
                    "cmd": cmd,
                    "detail": f"{tool} is not on PATH",
                }
            )
            continue

        output = run_command(resolved, cwd=workspace_root)
        ok = output.exit_code == 0
        if not ok:
            failures += 1
        results.append(
            {
                "available": True,
                "cmd": cmd,
                "resolved_cmd": output.cmd,
                "exit_code": output.exit_code,
                "stdout": output.stdout.strip(),
                "stderr": output.stderr.strip(),
            }
        )

    checks = {
        "artifacts_dir_writable": (workspace_root / "artifacts").parent.exists(),
        "templates_dir_present": (workspace_root / "templates").exists(),
    }

    payload = {
        "checks": checks,
        "tool_versions": results,
    }

    path = skill_run.run_dir / "doctor.json"
    write_json(path, payload)
    skill_run.record_artifact(path)

    if failures > 0:
        skill_run.add_note("Missing or failing tooling detected. Check doctor.json for details.")
        return skill_run.finalize("fail", emit_json=args.json)

    skill_run.add_note("Tooling checks passed.")
    return skill_run.finalize("pass", emit_json=args.json)
