from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from swarm_skills.commands import bench, pipeline, template_check
from tests.utils.shape_assert import assert_has_keys, assert_regex, assert_type


def _load_shape(name: str) -> dict:
    path = Path(__file__).resolve().parent / "golden" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _prepare_pipeline_workspace(tmp_path: Path) -> None:
    spec = tmp_path / "SPEC.todo.md"
    spec.write_text(
        """
# Product Spec

## Acceptance Criteria
- user can view todos
""".strip()
        + "\n",
        encoding="utf-8",
    )
    contracts = tmp_path / "artifacts" / "contracts" / "latest"
    contracts.mkdir(parents=True)
    (contracts / "api_contract.json").write_text(
        json.dumps({"schema_version": "1.0", "endpoints": [{"method": "GET", "path": "/api/todos"}]}),
        encoding="utf-8",
    )
    (contracts / "ROUTES.md").write_text("# ROUTES\n\n## /dashboard\n", encoding="utf-8")
    page = tmp_path / "src" / "dashboard"
    page.mkdir(parents=True)
    (page / "page.tsx").write_text("fetch('/api/todos')\n", encoding="utf-8")


def test_pipeline_result_matches_golden_shape(tmp_path):
    _prepare_pipeline_workspace(tmp_path)
    args = Namespace(
        workspace_root=str(tmp_path),
        spec="SPEC.todo.md",
        template=None,
        network=False,
        strict=False,
        stop_on_fail=True,
        steps="frontend_bind",
        json=False,
    )
    assert pipeline.run(args) == 0

    output = json.loads((tmp_path / "artifacts" / "pipeline" / "latest" / "pipeline_result.json").read_text(encoding="utf-8"))
    shape = _load_shape("pipeline_result.shape.json")
    assert_has_keys(output, shape["required_top_level_keys"])
    for key, expected_type in shape["type_assertions"].items():
        assert_type(output[key], expected_type)
    assert_regex(output["started_at"], r"^\d{4}-\d{2}-\d{2}T")
    assert_regex(output["ended_at"], r"^\d{4}-\d{2}-\d{2}T")
    for step in output["steps"]:
        assert_has_keys(step, shape["step_required_keys"])
        assert_type(step["notes"], "array")
        assert_type(step["status"], "str")


def test_bench_results_matches_golden_shape(tmp_path, monkeypatch):
    spec_dir = tmp_path / "examples" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "a.md").write_text("# Spec\n", encoding="utf-8")

    def fake_pipeline_run(args):
        latest = tmp_path / "artifacts" / "pipeline" / "latest"
        latest.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "overall_status": "pass",
            "steps": [{"step_name": "frontend_bind", "status": "pass", "duration_sec": 0.1}],
            "template": {"template_id": "local-node-http-crud", "template_version": "0.1.0"},
            "warnings_count": 0,
        }
        (latest / "pipeline_result.json").write_text(json.dumps(payload), encoding="utf-8")
        return 0

    monkeypatch.setattr(bench.pipeline, "run", fake_pipeline_run)
    args = Namespace(
        workspace_root=str(tmp_path),
        spec_dir="examples/specs",
        strict=False,
        network=False,
        template=None,
        json=False,
    )
    assert bench.run(args) == 0

    output = json.loads((tmp_path / "artifacts" / "bench" / "latest" / "bench_results.json").read_text(encoding="utf-8"))
    shape = _load_shape("bench_results.shape.json")
    assert_has_keys(output, shape["required_top_level_keys"])
    for key, expected_type in shape["type_assertions"].items():
        assert_type(output[key], expected_type)
    for row in output["results"]:
        assert_has_keys(row, shape["result_required_keys"])


def test_template_check_report_matches_golden_shape(tmp_path):
    template_dir = tmp_path / "templates" / "sample"
    template_dir.mkdir(parents=True)
    (template_dir / "template.json").write_text(
        json.dumps(
            {
                "id": "sample",
                "name": "Sample",
                "version": "0.1.0",
                "capabilities": ["api", "crud"],
                "boot": {"health_strategy": ["test_cmd:node scripts/no_network_check.js"], "inventory_cmd": ["node", "scripts/inventory.js"]},
            }
        ),
        encoding="utf-8",
    )
    args = Namespace(
        workspace_root=str(tmp_path),
        template=None,
        all=True,
        strict=False,
        json=False,
    )
    assert template_check.run(args) == 0

    output = json.loads((tmp_path / "artifacts" / "template_check" / "latest" / "report.json").read_text(encoding="utf-8"))
    shape = _load_shape("template_check_report.shape.json")
    assert_has_keys(output, shape["required_top_level_keys"])
    for key, expected_type in shape["type_assertions"].items():
        assert_type(output[key], expected_type)
    assert_has_keys(output["totals"], shape["totals_required_keys"])
    for row in output["templates"]:
        assert_has_keys(row, shape["template_required_keys"])
