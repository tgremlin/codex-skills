import json
from types import SimpleNamespace

from swarm_skills.commands import pipeline


def _prepare_frontend_bind_workspace(tmp_path):
    spec = tmp_path / "SPEC.md"
    spec.write_text("# Spec\n\n## Acceptance Criteria\n- user can view todos\n", encoding="utf-8")

    contracts = tmp_path / "artifacts" / "contracts" / "latest"
    contracts.mkdir(parents=True)
    (contracts / "api_contract.json").write_text(
        json.dumps({"endpoints": [{"method": "GET", "path": "/api/todos"}]}),
        encoding="utf-8",
    )
    (contracts / "ROUTES.md").write_text("# ROUTES\n\n## /dashboard\n", encoding="utf-8")

    frontend = tmp_path / "src" / "dashboard"
    frontend.mkdir(parents=True)
    (frontend / "page.tsx").write_text("fetch('/api/todos')\n", encoding="utf-8")


def test_pipeline_result_json_written_on_success(tmp_path):
    _prepare_frontend_bind_workspace(tmp_path)
    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        spec="SPEC.md",
        template=None,
        network=False,
        strict=False,
        stop_on_fail=True,
        steps="frontend_bind",
        json=False,
    )
    code = pipeline.run(args)
    assert code == 0

    latest = tmp_path / "artifacts" / "pipeline" / "latest"
    result = json.loads((latest / "pipeline_result.json").read_text(encoding="utf-8"))
    assert result["schema_version"] == "1.0"
    assert result["overall_status"] == "pass"
    assert "handoff_contract_path" in result
    assert "handoff_contract_sha256" in result
    assert "triage" in result
    assert result["strict_mode"] is False
    assert result["warnings_count"] == 0
    assert result["steps"][0]["step_name"] == "frontend_bind"
    assert result["steps"][0]["status"] == "pass"
    assert result["steps"][0]["summary_json_path"]


def test_pipeline_result_json_written_on_failure_with_strict(tmp_path):
    _prepare_frontend_bind_workspace(tmp_path)
    config_dir = tmp_path / "skills" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "exemptions.json").write_text(
        json.dumps(
            {
                "exemptions": [
                    {
                        "id": "EX-EXPIRED-001",
                        "rule": "frontend_route_unlinked",
                        "path_or_pattern": "/unused",
                        "reason": "legacy path",
                        "owner": "frontend-team",
                        "expires_on": "2000-01-01",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        spec="SPEC.md",
        template=None,
        network=False,
        strict=True,
        stop_on_fail=True,
        steps="frontend_bind",
        json=False,
    )
    code = pipeline.run(args)
    assert code == 1

    latest = tmp_path / "artifacts" / "pipeline" / "latest"
    result = json.loads((latest / "pipeline_result.json").read_text(encoding="utf-8"))
    assert result["schema_version"] == "1.0"
    assert result["overall_status"] == "fail"
    assert "handoff_contract_path" in result
    assert "handoff_contract_sha256" in result
    assert "triage" in result
    assert result["strict_mode"] is True
    assert result["steps"][0]["step_name"] == "frontend_bind"
    assert result["steps"][0]["status"] == "fail"
