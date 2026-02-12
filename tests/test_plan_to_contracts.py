from types import SimpleNamespace

import json

from swarm_skills.commands import plan_to_contracts


def _write_spec(path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_plan_to_contracts_happy_path(tmp_path):
    spec = tmp_path / "SPEC.md"
    _write_spec(
        spec,
        """
# Product Spec

## Acceptance Criteria
- User can create a todo
- User can mark todo complete
- User can delete a todo
""".strip(),
    )

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        spec="SPEC.md",
        test_plan_source=None,
        json=False,
    )

    code = plan_to_contracts.run(args)
    assert code == 0

    latest = tmp_path / "artifacts" / "contracts" / "latest"
    assert (latest / "API_CONTRACT.md").exists()
    assert (latest / "DATA_MODEL.md").exists()
    assert (latest / "ROUTES.md").exists()
    assert (latest / "TEST_PLAN.md").exists()
    assert (latest / "api_contract.json").exists()
    assert (latest / "contracts_summary.json").exists()
    api_contract = json.loads((latest / "api_contract.json").read_text(encoding="utf-8"))
    assert api_contract["schema_version"] == "1.0"


def test_plan_to_contracts_fails_on_missing_mapping(tmp_path):
    spec = tmp_path / "SPEC.md"
    custom_test_plan = tmp_path / "custom_TEST_PLAN.md"

    _write_spec(
        spec,
        """
# Product Spec

Acceptance Criteria:
- User can create a todo
- User can delete a todo
""".strip(),
    )

    custom_test_plan.write_text(
        """
# TEST_PLAN

| test_id | acceptance_ids | layers | description |
|---|---|---|---|
| TC-001 | AC-001 | api | only first acceptance mapped |
""".strip()
        + "\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        spec="SPEC.md",
        test_plan_source="custom_TEST_PLAN.md",
        json=False,
    )

    code = plan_to_contracts.run(args)
    assert code == 1

    gate_report = tmp_path / "artifacts" / "contracts" / "latest" / "GateReport.md"
    assert gate_report.exists()
    text = gate_report.read_text(encoding="utf-8")
    assert "Status: FAIL" in text
    assert "AC-002" in text
