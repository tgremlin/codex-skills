import json
from types import SimpleNamespace

from swarm_skills.commands import pipeline


def test_pipeline_triage_on_fail_generates_triage_artifacts(tmp_path):
    spec = tmp_path / "SPEC.md"
    spec.write_text("# Spec\n\n## Acceptance Criteria\n- one\n", encoding="utf-8")

    contracts = tmp_path / "artifacts" / "contracts" / "latest"
    contracts.mkdir(parents=True)
    (contracts / "api_contract.json").write_text(
        json.dumps({"schema_version": "1.0", "endpoints": [{"method": "GET", "path": "/api/todos"}]}),
        encoding="utf-8",
    )
    (contracts / "ROUTES.md").write_text("# ROUTES\n\n## /reports\n", encoding="utf-8")

    frontend = tmp_path / "src" / "home"
    frontend.mkdir(parents=True)
    (frontend / "page.tsx").write_text("export const x = 1\n", encoding="utf-8")

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        spec="SPEC.md",
        template=None,
        network=False,
        strict=False,
        stop_on_fail=True,
        steps="frontend_bind",
        triage_on_fail=True,
        json=False,
    )
    code = pipeline.run(args)
    assert code == 1

    triage_latest = tmp_path / "artifacts" / "triage" / "latest"
    assert (triage_latest / "root_cause.md").exists()
    assert (triage_latest / "patch_plan.md").exists()

    pipeline_result = json.loads((tmp_path / "artifacts" / "pipeline" / "latest" / "pipeline_result.json").read_text(encoding="utf-8"))
    triage = pipeline_result["triage"]
    assert triage is not None
    assert triage["status"] in {"pass", "fail"}
    assert triage["root_cause_path"] == "artifacts/triage/latest/root_cause.md"
    assert triage["patch_plan_path"] == "artifacts/triage/latest/patch_plan.md"
