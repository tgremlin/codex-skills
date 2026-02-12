from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_skills(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "skills", *args],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _prepare_pipeline_workspace(workspace: Path) -> None:
    (workspace / "SPEC.md").write_text(
        "# Spec\n\n## Acceptance Criteria\n- user can view todos\n",
        encoding="utf-8",
    )
    contracts = workspace / "artifacts" / "contracts" / "latest"
    contracts.mkdir(parents=True, exist_ok=True)
    (contracts / "api_contract.json").write_text(
        json.dumps({"schema_version": "1.0", "endpoints": [{"method": "GET", "path": "/api/todos"}]}),
        encoding="utf-8",
    )
    (contracts / "ROUTES.md").write_text("# ROUTES\n\n## /dashboard\n", encoding="utf-8")
    page = workspace / "src" / "dashboard"
    page.mkdir(parents=True, exist_ok=True)
    (page / "page.tsx").write_text("fetch('/api/todos')\n", encoding="utf-8")


def test_pipeline_orchestrator_stdout_is_single_json(tmp_path):
    _prepare_pipeline_workspace(tmp_path)
    result = _run_skills(
        [
            "pipeline",
            "--workspace-root",
            str(tmp_path),
            "--spec",
            "SPEC.md",
            "--steps",
            "frontend_bind",
            "--orchestrator",
        ]
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1.0"
    assert payload["overall_status"] == "pass"
    assert payload["steps"][0]["step_name"] == "frontend_bind"
    assert "orchestrator mode" in result.stderr


def test_non_pipeline_orchestrator_stdout_is_single_json(tmp_path):
    template_dir = tmp_path / "templates" / "sample"
    template_dir.mkdir(parents=True)
    (template_dir / "template.json").write_text(
        json.dumps(
            {
                "id": "sample",
                "name": "Sample",
                "version": "0.1.0",
                "capabilities": ["api", "crud"],
                "boot": {
                    "health_strategy": ["test_cmd:node scripts/no_network_check.js"],
                    "inventory_cmd": ["node", "scripts/inventory.js"],
                },
            }
        ),
        encoding="utf-8",
    )

    result = _run_skills(
        [
            "template_check",
            "--workspace-root",
            str(tmp_path),
            "--all",
            "--orchestrator",
            "--json",
        ]
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["skill"] == "template_check"
    assert payload["status"] == "pass"
    assert "orchestrator mode" in result.stderr
