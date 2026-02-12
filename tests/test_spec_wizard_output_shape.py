from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from swarm_skills.commands import spec_wizard


def _write_repo_fixture(repo_root: Path) -> None:
    (repo_root / "app").mkdir(parents=True, exist_ok=True)
    (repo_root / "package.json").write_text(
        json.dumps(
            {
                "name": "warehouse-suite",
                "dependencies": {
                    "next": "14.2.0",
                    "react": "18.3.1",
                    "@supabase/supabase-js": "2.0.0",
                },
            }
        ),
        encoding="utf-8",
    )
    (repo_root / ".env.example").write_text(
        "NEXT_PUBLIC_SUPABASE_URL=\nNEXT_PUBLIC_SUPABASE_ANON_KEY=\n",
        encoding="utf-8",
    )
    (repo_root / "app" / "page.tsx").write_text("export default function Page(){return null}\n", encoding="utf-8")



def test_spec_wizard_generates_required_shape_and_schema(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    target_repo = tmp_path / "target_repo"
    target_repo.mkdir(parents=True)

    _write_repo_fixture(target_repo)

    answers_path = tmp_path / "answers.json"
    answers_path.write_text(
        json.dumps(
            {
                "app_name": "Warehouse Suite",
                "roles": ["admin", "dispatcher", "operator"],
                "auth_requirement": "use Supabase Auth",
                "entities": [
                    {"name": "Order", "fields": ["id", "status", "customer_id"]},
                    {"name": "Customer", "fields": ["id", "name", "email"]},
                ],
                "operations": [
                    {"name": "Create order", "actor": "dispatcher", "inputs": "order payload", "output": "order id", "error_cases": "validation error"},
                    {"name": "List orders", "actor": "operator", "inputs": "filter options", "output": "order list", "error_cases": "unauthorized"},
                    {"name": "Update order status", "actor": "operator", "inputs": "order id and status", "output": "updated order", "error_cases": "not found"},
                    {"name": "Delete order", "actor": "admin", "inputs": "order id", "output": "deletion confirmation", "error_cases": "not found"},
                    {"name": "Assign order", "actor": "dispatcher", "inputs": "order id and assignee", "output": "assignment result", "error_cases": "conflict"},
                ],
                "non_goals": [
                    "No mobile redesign.",
                    "No payment gateway implementation.",
                    "No analytics dashboard buildout.",
                ],
                "definition_of_done": "All ACs map to tests and output gate is pass or warn.",
            }
        ),
        encoding="utf-8",
    )

    args = Namespace(
        workspace_root=str(workspace),
        repo=str(target_repo),
        out=None,
        app_name=None,
        flow_next=False,
        epic=None,
        run_contracts=False,
        run_pipeline=False,
        non_interactive=True,
        answers=str(answers_path),
        json=False,
    )

    code = spec_wizard.run(args)
    assert code == 0

    latest = workspace / "artifacts" / "spec_wizard" / "latest"
    summary = json.loads((latest / "summary.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == "1.0"

    spec_json = json.loads((latest / "spec.json").read_text(encoding="utf-8"))
    assert spec_json["schema_version"] == "1.0"
    assert spec_json["acceptance_criteria"]

    tests = spec_json["test_plan"]
    mapped = {row["id"]: 0 for row in spec_json["acceptance_criteria"]}
    for row in tests:
        mapped[row["acceptance_criteria"]] += 1
        assert row["layer"] in {"ui", "api", "db"}

    assert all(count > 0 for count in mapped.values())

    spec_path = Path(summary["generated_spec_path"])
    assert spec_path.exists()
    text = spec_path.read_text(encoding="utf-8")

    assert "## Scope" in text
    assert "## Non-goals" in text
    assert "## Acceptance Criteria" in text
    assert "## Key Entities / Data Model Notes" in text
    assert "## Endpoints / Operations" in text
    assert "## TEST_PLAN" in text
    assert "| Test ID | Acceptance Criteria | Layer | Type | Description |" in text
