import shutil
from pathlib import Path
from types import SimpleNamespace

from swarm_skills.commands import fullstack_test_harness


def _prepare_workspace(tmp_path: Path, template_name: str) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "templates" / template_name
    dst = tmp_path / "templates" / template_name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)

    plan_dir = tmp_path / "artifacts" / "contracts" / "latest"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "TEST_PLAN.md").write_text(
        """
# TEST_PLAN

| test_id | acceptance_ids | layers | description |
|---|---|---|---|
| TC-001 | AC-001 | api | api path |
| TC-002 | AC-001 | db | db path |
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_fullstack_harness_no_network_passes_for_local_template(tmp_path):
    _prepare_workspace(tmp_path, "local-node-http-crud")

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        test_plan="artifacts/contracts/latest/TEST_PLAN.md",
        template="local-node-http-crud",
        network=False,
        health_timeout_sec=10,
        json=False,
    )

    code = fullstack_test_harness.run(args)
    assert code == 0

    latest = tmp_path / "artifacts" / "tests" / "latest"
    assert (latest / "summary.json").exists()
    assert (latest / "GateReport.md").exists()
    assert '"status": "pass"' in (latest / "summary.json").read_text(encoding="utf-8")


def test_fullstack_harness_failure_has_actionable_gatereport(tmp_path):
    _prepare_workspace(tmp_path, "nextjs-api-routes-sqlite")

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        test_plan="artifacts/contracts/latest/TEST_PLAN.md",
        template="nextjs-api-routes-sqlite",
        network=False,
        health_timeout_sec=5,
        json=False,
    )

    code = fullstack_test_harness.run(args)
    assert code == 1

    gate_report = tmp_path / "artifacts" / "tests" / "latest" / "GateReport.md"
    text = gate_report.read_text(encoding="utf-8")
    assert "Status: FAIL" in text
    assert "Failing test IDs" in text
    assert "Next fix steps" in text


def test_fullstack_harness_no_network_passes_for_nextjs_prisma_template(tmp_path):
    _prepare_workspace(tmp_path, "nextjs-prisma-sqlite-crud")

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        test_plan="artifacts/contracts/latest/TEST_PLAN.md",
        template="nextjs-prisma-sqlite-crud",
        network=False,
        health_timeout_sec=10,
        json=False,
    )

    code = fullstack_test_harness.run(args)
    assert code == 0
