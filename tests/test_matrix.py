import json
from types import SimpleNamespace

from swarm_skills.commands import matrix


def _write_template(root, template_id):
    template_dir = root / "templates" / template_id
    template_dir.mkdir(parents=True)
    (template_dir / "template.json").write_text(
        json.dumps({"id": template_id, "name": template_id, "version": "0.1.0"}),
        encoding="utf-8",
    )


def test_matrix_writes_matrix_json_structure(tmp_path, monkeypatch):
    spec_dir = tmp_path / "examples" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "s1.md").write_text("# Spec 1\n", encoding="utf-8")
    (spec_dir / "s2.md").write_text("# Spec 2\n", encoding="utf-8")

    _write_template(tmp_path, "t1")
    _write_template(tmp_path, "t2")

    def fake_pipeline_run(args):
        latest = tmp_path / "artifacts" / "pipeline" / "latest"
        latest.mkdir(parents=True, exist_ok=True)
        status = "fail" if args.template == "t2" and str(args.spec).endswith("s2.md") else "pass"
        payload = {
            "schema_version": "1.0",
            "overall_status": status,
            "warnings_count": 0,
            "template": {"template_id": args.template, "template_version": "0.1.0"},
            "steps": [],
        }
        (latest / "pipeline_result.json").write_text(json.dumps(payload), encoding="utf-8")
        return 1 if status == "fail" else 0

    monkeypatch.setattr(matrix.pipeline, "run", fake_pipeline_run)

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        spec_dir="examples/specs",
        templates="all",
        strict=False,
        network=False,
        limit=10,
        json=False,
    )
    code = matrix.run(args)
    assert code == 1

    latest = tmp_path / "artifacts" / "matrix" / "latest"
    matrix_json = json.loads((latest / "matrix.json").read_text(encoding="utf-8"))
    assert matrix_json["schema_version"] == "1.0"
    assert "matrix" in matrix_json
    assert len(matrix_json["matrix"]) == 4
    assert all("spec_path" in row and "template_id" in row and "status" in row for row in matrix_json["matrix"])
