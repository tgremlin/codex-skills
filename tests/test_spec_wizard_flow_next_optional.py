from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from swarm_skills.commands import spec_wizard


def _base_repo(repo_root: Path) -> None:
    (repo_root / "package.json").write_text(
        json.dumps({"name": "flow-next-target", "dependencies": {"next": "14.2.0", "react": "18.3.1"}}),
        encoding="utf-8",
    )
    (repo_root / "app").mkdir(parents=True, exist_ok=True)
    (repo_root / "app" / "page.tsx").write_text("export default function Page(){return null}\n", encoding="utf-8")


def _answers(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "app_name": "Flow Next Target",
                "roles": ["admin", "operator"],
                "auth_requirement": "none",
                "entities": [
                    {"name": "Task", "fields": ["id", "title", "status"]},
                    {"name": "User", "fields": ["id", "email", "role"]},
                ],
                "operations": [
                    "admin:Create task",
                    "admin:List tasks",
                    "admin:Update task",
                    "admin:Delete task",
                    "operator:View task",
                ],
                "non_goals": ["No billing integration.", "No visual redesign.", "No analytics integration."],
                "definition_of_done": "ACs and test plan are generated.",
            }
        ),
        encoding="utf-8",
    )


def test_flow_next_flag_does_not_fail_when_flow_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    target_repo = tmp_path / "target"
    target_repo.mkdir(parents=True)
    _base_repo(target_repo)

    answers = tmp_path / "answers.json"
    _answers(answers)

    args = Namespace(
        workspace_root=str(workspace),
        repo=str(target_repo),
        out=None,
        app_name=None,
        flow_next=True,
        epic=None,
        run_contracts=False,
        run_pipeline=False,
        non_interactive=True,
        answers=str(answers),
        json=False,
    )

    code = spec_wizard.run(args)
    assert code == 0

    latest = workspace / "artifacts" / "spec_wizard" / "latest"
    gate = (latest / "GateReport.md").read_text(encoding="utf-8")
    assert "Flow-Next requested but .flow/bin/flowctl is unavailable" in gate


def test_flow_next_import_maps_tasks_when_flow_exists(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    target_repo = tmp_path / "target"
    target_repo.mkdir(parents=True)
    _base_repo(target_repo)

    flowctl = target_repo / ".flow" / "bin" / "flowctl"
    flowctl.parent.mkdir(parents=True, exist_ok=True)
    flowctl.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "cmd=\"$1\"\n"
        "if [ \"$cmd\" = \"validate\" ]; then\n"
        "  echo 'Validation for all epics:'\n"
        "  echo '  Epics: 1'\n"
        "  echo '  Tasks: 2'\n"
        "  echo '  Valid: True'\n"
        "elif [ \"$cmd\" = \"epics\" ]; then\n"
        "  echo 'Epics (1):'\n"
        "  echo '  [open] fn-9-abc: Flow Fixture Epic (1/2 tasks done)'\n"
        "elif [ \"$cmd\" = \"tasks\" ]; then\n"
        "  echo 'Tasks (2):'\n"
        "  echo '  [done] fn-9-abc.1: Configure auth provider'\n"
        "  echo '  [todo] fn-9-abc.2: Create database migration'\n"
        "else\n"
        "  echo \"Unsupported command: $cmd\" >&2\n"
        "  exit 1\n"
        "fi\n",
        encoding="utf-8",
    )
    flowctl.chmod(0o755)

    answers = tmp_path / "answers.json"
    _answers(answers)

    args = Namespace(
        workspace_root=str(workspace),
        repo=str(target_repo),
        out=None,
        app_name=None,
        flow_next=True,
        epic="fn-9-abc",
        run_contracts=False,
        run_pipeline=False,
        non_interactive=True,
        answers=str(answers),
        json=False,
    )

    code = spec_wizard.run(args)
    assert code == 0

    latest = workspace / "artifacts" / "spec_wizard" / "latest"
    trace = json.loads((latest / "trace_map.json").read_text(encoding="utf-8"))

    assert trace["flow_next"]["enabled"] is True
    assert trace["tasks"]["fn-9-abc.1"]["mapped_acceptance_criteria"]
    assert trace["tasks"]["fn-9-abc.2"]["mapped_acceptance_criteria"]
