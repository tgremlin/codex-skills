import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from swarm_skills.commands import pipeline


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def test_handoff_contract_file_schema():
    path = Path(__file__).resolve().parents[1] / "skills" / "handoff_contract.json"
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["schema_version"] == "1.0"
    assert "steps" in payload and payload["steps"]
    assert all("name" in step and "command" in step for step in payload["steps"])


def test_pipeline_result_includes_handoff_contract_sha(tmp_path):
    contract_src = Path(__file__).resolve().parents[1] / "skills" / "handoff_contract.json"
    contract_dst = tmp_path / "skills" / "handoff_contract.json"
    contract_dst.parent.mkdir(parents=True)
    shutil.copy(str(contract_src), contract_dst)

    spec = tmp_path / "SPEC.md"
    spec.write_text("# Spec\n\n## Acceptance Criteria\n- one\n", encoding="utf-8")
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

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        spec="SPEC.md",
        template=None,
        network=False,
        strict=False,
        stop_on_fail=True,
        steps="frontend_bind",
        triage_on_fail=False,
        json=False,
    )
    assert pipeline.run(args) == 0
    result = json.loads((tmp_path / "artifacts" / "pipeline" / "latest" / "pipeline_result.json").read_text(encoding="utf-8"))
    assert result["handoff_contract_path"] == "skills/handoff_contract.json"
    assert result["handoff_contract_sha256"] == _sha256(contract_dst)
