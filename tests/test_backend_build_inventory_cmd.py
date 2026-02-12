import json
from types import SimpleNamespace

from swarm_skills.commands import backend_build


def _write_contract(tmp_path):
    contracts = tmp_path / "artifacts" / "contracts" / "latest"
    contracts.mkdir(parents=True)
    (contracts / "api_contract.json").write_text(
        json.dumps(
            {
                "endpoints": [
                    {"id": "EP-001", "method": "GET", "path": "/api/todos", "required": True},
                ]
            }
        ),
        encoding="utf-8",
    )


def _write_template(tmp_path, template_id, inventory_script_name):
    template_dir = tmp_path / "templates" / template_id
    template_dir.mkdir(parents=True)
    (template_dir / "template.json").write_text(
        json.dumps(
            {
                "id": template_id,
                "name": "Template",
                "description": "Test template",
                "version": "0.1.0",
                "status": "active",
                "risk_flags": [],
                "capabilities": ["api", "crud"],
                "runbook": {},
                "boot": {
                    "inventory_cmd": ["python3", inventory_script_name],
                },
            }
        ),
        encoding="utf-8",
    )
    return template_dir


def test_backend_build_accepts_valid_inventory_cmd_schema(tmp_path):
    _write_contract(tmp_path)
    template_dir = _write_template(tmp_path, "template-ok", "inventory_ok.py")
    (template_dir / "inventory_ok.py").write_text(
        "import json\nprint(json.dumps({'endpoints':[{'method':'GET','path':'/api/todos'}]}))\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        contracts="artifacts/contracts/latest/api_contract.json",
        template="template-ok",
        backend_root=None,
        json=False,
    )
    code = backend_build.run(args)
    assert code == 0

    coverage = json.loads((tmp_path / "artifacts" / "backend" / "latest" / "contract_coverage.json").read_text(encoding="utf-8"))
    assert coverage["schema_version"] == "1.0"
    assert coverage["missing_required"] == []


def test_backend_build_fails_for_invalid_inventory_cmd_schema(tmp_path):
    _write_contract(tmp_path)
    template_dir = _write_template(tmp_path, "template-bad", "inventory_bad.py")
    (template_dir / "inventory_bad.py").write_text(
        "import json\nprint(json.dumps({'bad_key': []}))\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        contracts="artifacts/contracts/latest/api_contract.json",
        template="template-bad",
        backend_root=None,
        json=False,
    )
    code = backend_build.run(args)
    assert code == 1

    gate = (tmp_path / "artifacts" / "backend" / "latest" / "GateReport.md").read_text(encoding="utf-8")
    assert "inventory_cmd output must include `endpoints` array" in gate
