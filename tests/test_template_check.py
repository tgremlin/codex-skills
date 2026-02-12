import json
from types import SimpleNamespace

from swarm_skills.commands import template_check


def _write_template_json(root, template_id, payload):
    template_dir = root / "templates" / template_id
    template_dir.mkdir(parents=True)
    (template_dir / "template.json").write_text(json.dumps(payload), encoding="utf-8")


def test_template_check_valid_template_passes(tmp_path):
    _write_template_json(
        tmp_path,
        "valid-template",
        {
            "id": "valid-template",
            "name": "Valid Template",
            "version": "1.0.0",
            "capabilities": ["api", "crud", "db", "ui"],
            "boot": {"health_strategy": ["test_cmd:node scripts/no_network_check.js"], "inventory_cmd": ["node", "scripts/inventory.js"]},
        },
    )

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        template="valid-template",
        all=False,
        strict=False,
        json=False,
    )
    code = template_check.run(args)
    assert code == 0
    report = json.loads((tmp_path / "artifacts" / "template_check" / "latest" / "report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "1.0"
    assert report["overall_status"] == "pass"


def test_template_check_missing_required_fails(tmp_path):
    _write_template_json(
        tmp_path,
        "invalid-template",
        {
            "id": "invalid-template",
            "name": "Invalid Template",
            "capabilities": ["api"],
            "boot": {"health_strategy": []},
        },
    )
    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        template="invalid-template",
        all=False,
        strict=False,
        json=False,
    )
    code = template_check.run(args)
    assert code == 1
    gate = (tmp_path / "artifacts" / "template_check" / "latest" / "GateReport.md").read_text(encoding="utf-8")
    assert "Missing required field `version`." in gate
    assert "health_strategy" in gate


def test_template_check_missing_recommended_warns_and_fails_under_strict(tmp_path):
    _write_template_json(
        tmp_path,
        "warn-template",
        {
            "id": "warn-template",
            "name": "Warn Template",
            "version": "1.0.0",
            "capabilities": ["api", "crud"],
            "boot": {"health_strategy": ["test_cmd:node scripts/no_network_check.js"]},
        },
    )
    base_args = {
        "workspace_root": str(tmp_path),
        "template": "warn-template",
        "all": False,
        "json": False,
    }
    non_strict_code = template_check.run(SimpleNamespace(**base_args, strict=False))
    assert non_strict_code == 0
    non_strict_report = json.loads((tmp_path / "artifacts" / "template_check" / "latest" / "report.json").read_text(encoding="utf-8"))
    assert non_strict_report["schema_version"] == "1.0"
    assert non_strict_report["overall_status"] == "warn"

    strict_code = template_check.run(SimpleNamespace(**base_args, strict=True))
    assert strict_code == 1
    strict_report = json.loads((tmp_path / "artifacts" / "template_check" / "latest" / "report.json").read_text(encoding="utf-8"))
    assert strict_report["schema_version"] == "1.0"
    assert strict_report["overall_status"] == "fail"
