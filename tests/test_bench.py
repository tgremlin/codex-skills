import json
from types import SimpleNamespace

from swarm_skills.commands import bench


def test_bench_collects_pipeline_results_with_mocked_pipeline(tmp_path, monkeypatch):
    spec_dir = tmp_path / "examples" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "a_ok.md").write_text("# Spec\n", encoding="utf-8")
    (spec_dir / "b_fail.md").write_text("# Spec\n", encoding="utf-8")

    calls = []

    def fake_pipeline_run(args):
        calls.append(args)
        status = "fail" if str(args.spec).endswith("b_fail.md") else "pass"
        payload = {
            "overall_status": status,
            "steps": [
                {"step_name": "template_select", "status": status if status == "fail" else "pass", "duration_sec": 0.1},
                {"step_name": "frontend_bind", "status": "warn" if status == "pass" else "skipped", "duration_sec": 0.2},
            ],
            "template": {"template_id": "local-node-http-crud", "template_version": "0.1.0"},
            "warnings_count": 1 if status == "pass" else 0,
        }
        latest = tmp_path / "artifacts" / "pipeline" / "latest"
        latest.mkdir(parents=True, exist_ok=True)
        (latest / "pipeline_result.json").write_text(json.dumps(payload), encoding="utf-8")
        return 1 if status == "fail" else 0

    monkeypatch.setattr(bench.pipeline, "run", fake_pipeline_run)

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        spec_dir="examples/specs",
        strict=True,
        network=False,
        template="local-node-http-crud",
        json=False,
    )
    code = bench.run(args)
    assert code == 1
    assert len(calls) == 2
    assert all(call.strict is True for call in calls)

    latest = tmp_path / "artifacts" / "bench" / "latest"
    results = json.loads((latest / "bench_results.json").read_text(encoding="utf-8"))
    assert results["schema_version"] == "1.0"
    assert results["overall_status"] == "fail"
    assert len(results["results"]) == 2
    assert results["results"][0]["spec_path"].endswith("a_ok.md")
    assert results["results"][1]["spec_path"].endswith("b_fail.md")
