import json
from types import SimpleNamespace

from swarm_skills.commands import triage_and_patch


def test_triage_and_patch_generates_plan(tmp_path):
    gate = tmp_path / "GateReport.md"
    gate.write_text(
        """
# Frontend Bind GateReport

Status: FAIL

Linked endpoints not in contract:
- /reports -> /api/unknown
""".strip()
        + "\n",
        encoding="utf-8",
    )

    artifacts_dir = tmp_path / "artifacts"
    (artifacts_dir / "frontend" / "latest").mkdir(parents=True)
    (artifacts_dir / "frontend" / "latest" / "mock_data_report.json").write_text(
        json.dumps({"findings": []}), encoding="utf-8"
    )

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        gate_report=["GateReport.md"],
        logs=None,
        artifacts_root="artifacts",
        contracts=None,
        json=False,
    )

    code = triage_and_patch.run(args)
    assert code == 0

    latest = tmp_path / "artifacts" / "triage" / "latest"
    assert (latest / "root_cause.md").exists()
    assert (latest / "patch_plan.md").exists()
    summary_payload = json.loads((latest / "summary_payload.json").read_text(encoding="utf-8"))
    assert summary_payload["schema_version"] == "1.0"
    assert summary_payload["classification"] in {
        "env/bootstrap",
        "contract mismatch",
        "backend runtime",
        "frontend binding",
        "db persistence",
        "test flakiness",
    }


def test_triage_prefers_structured_backend_coverage_and_writes_rerun_recipe(tmp_path):
    gate = tmp_path / "GateReport.md"
    gate.write_text("# Generic GateReport\n\nStatus: FAIL\n", encoding="utf-8")

    artifacts_dir = tmp_path / "artifacts"
    (artifacts_dir / "backend" / "latest").mkdir(parents=True)
    (artifacts_dir / "backend" / "latest" / "contract_coverage.json").write_text(
        json.dumps(
            {
                "missing_required": [{"method": "GET", "path": "/api/todos"}],
                "mismatched_methods": [],
                "mismatched_paths": [],
            }
        ),
        encoding="utf-8",
    )

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        gate_report=["GateReport.md"],
        logs=None,
        artifacts_root="artifacts",
        contracts=None,
        json=False,
    )
    code = triage_and_patch.run(args)
    assert code == 0

    latest = tmp_path / "artifacts" / "triage" / "latest"
    payload = json.loads((latest / "summary_payload.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["classification"] == "contract mismatch"
    assert payload["signal_source"] == "backend_coverage"
    summary_json = json.loads((latest / "summary.json").read_text(encoding="utf-8"))
    assert summary_json["schema_version"] == "1.0"
    patch_plan = (latest / "patch_plan.md").read_text(encoding="utf-8")
    assert "python -m skills backend_build --contracts artifacts/contracts/latest/api_contract.json" in patch_plan
    assert "python -m skills pipeline --spec examples/SPEC.todo.md" in patch_plan
