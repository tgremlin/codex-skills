import json
from types import SimpleNamespace

from swarm_skills.commands import bench


def test_bench_append_history_writes_jsonl(tmp_path, monkeypatch):
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

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        spec_dir="examples/specs",
        strict=False,
        network=False,
        template=None,
        append_history=True,
        json=False,
    )
    assert bench.run(args) == 0

    history_path = tmp_path / "artifacts" / "bench" / "history.jsonl"
    assert history_path.exists()
    lines = [line for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["strict_mode"] is False
    assert entry["overall_counts"]["pass"] == 1
    assert entry["per_spec"][0]["status"] == "pass"
