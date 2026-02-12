from __future__ import annotations

from pathlib import Path
from typing import Any

from swarm_skills.runtime import SkillRun, write_json


def run_stub(skill_name: str, args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    skill_run = SkillRun(skill=skill_name, workspace_root=workspace_root)

    payload = {
        "message": "Skill scaffolded but not implemented yet.",
        "next_steps": [
            "Implement deterministic artifact generation.",
            "Implement gate checks and non-zero exits on failure.",
            "Wire into fullstack_test_harness.",
        ],
        "skill": skill_name,
        "status": "stub",
    }

    artifact_path = skill_run.run_dir / "stub.json"
    write_json(artifact_path, payload)
    skill_run.record_artifact(artifact_path)
    skill_run.add_note("Stub command executed.")
    return skill_run.finalize("fail", emit_json=getattr(args, "json", False))
