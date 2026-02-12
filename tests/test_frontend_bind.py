import json
from types import SimpleNamespace

from swarm_skills.commands import frontend_bind


def test_frontend_bind_detects_mock_and_unlinked_routes(tmp_path):
    contracts = tmp_path / "artifacts" / "contracts" / "latest"
    contracts.mkdir(parents=True)

    api_contract = {
        "endpoints": [
            {"method": "GET", "path": "/api/todos"},
            {"method": "GET", "path": "/api/health"},
        ]
    }
    (contracts / "api_contract.json").write_text(json.dumps(api_contract), encoding="utf-8")
    (contracts / "ROUTES.md").write_text(
        """
# ROUTES
## /
## /reports
## /mock-route
""".strip()
        + "\n",
        encoding="utf-8",
    )

    frontend = tmp_path / "frontend"
    (frontend / "src" / "home").mkdir(parents=True)
    (frontend / "src" / "reports").mkdir(parents=True)
    (frontend / "src" / "mock").mkdir(parents=True)

    (frontend / "src" / "home" / "page.tsx").write_text("fetch('/api/todos')\n", encoding="utf-8")
    (frontend / "src" / "reports" / "page.tsx").write_text("export const view='reports'\n", encoding="utf-8")
    (frontend / "src" / "mock" / "view.tsx").write_text(
        "const mockTodos = [{id:1,title:'demo'}]; const route='/mock-route';\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        contracts_dir="artifacts/contracts/latest",
        frontend_root="frontend",
        allowlist_config=None,
        template=None,
        json=False,
    )

    code = frontend_bind.run(args)
    assert code == 1

    gate_report = tmp_path / "artifacts" / "frontend" / "latest" / "GateReport.md"
    assert gate_report.exists()
    text = gate_report.read_text(encoding="utf-8")
    assert "Status: FAIL" in text
    assert "/reports" in text
    assert "Mock data findings" in text


def test_frontend_bind_prefers_api_client_wrapper(tmp_path):
    contracts = tmp_path / "artifacts" / "contracts" / "latest"
    contracts.mkdir(parents=True)
    (contracts / "api_contract.json").write_text(
        json.dumps({"endpoints": [{"method": "GET", "path": "/api/todos"}]}),
        encoding="utf-8",
    )
    (contracts / "ROUTES.md").write_text("# ROUTES\n\n## /dashboard\n", encoding="utf-8")

    frontend = tmp_path / "frontend"
    (frontend / "src" / "dashboard").mkdir(parents=True)
    (frontend / "src" / "lib").mkdir(parents=True)

    (frontend / "src" / "lib" / "apiClient.ts").write_text(
        """
export async function listTodos() {
  return fetch('/api/todos');
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (frontend / "src" / "dashboard" / "page.tsx").write_text(
        """
import { listTodos } from "../lib/apiClient";
export function Page() { listTodos(); return null; }
""".strip()
        + "\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        contracts_dir="artifacts/contracts/latest",
        frontend_root="frontend",
        allowlist_config=None,
        strict=False,
        template=None,
        json=False,
    )
    code = frontend_bind.run(args)
    assert code == 0

    usage = json.loads((tmp_path / "artifacts" / "frontend" / "latest" / "api_usage.json").read_text(encoding="utf-8"))
    assert usage["schema_version"] == "1.0"
    assert usage["detection_strategy"] == "api_client_preferred"
    assert usage["route_usage"][0]["linked_endpoints"] == ["/api/todos"]


def test_frontend_bind_heuristic_fallback_without_wrapper(tmp_path):
    contracts = tmp_path / "artifacts" / "contracts" / "latest"
    contracts.mkdir(parents=True)
    (contracts / "api_contract.json").write_text(
        json.dumps({"endpoints": [{"method": "GET", "path": "/api/todos"}]}),
        encoding="utf-8",
    )
    (contracts / "ROUTES.md").write_text("# ROUTES\n\n## /dashboard\n", encoding="utf-8")

    frontend = tmp_path / "frontend"
    (frontend / "src" / "dashboard").mkdir(parents=True)
    (frontend / "src" / "dashboard" / "page.tsx").write_text(
        "fetch('/api/todos')\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        contracts_dir="artifacts/contracts/latest",
        frontend_root="frontend",
        allowlist_config=None,
        strict=False,
        template=None,
        json=False,
    )
    code = frontend_bind.run(args)
    assert code == 0

    usage = json.loads((tmp_path / "artifacts" / "frontend" / "latest" / "api_usage.json").read_text(encoding="utf-8"))
    assert usage["schema_version"] == "1.0"
    assert usage["detection_strategy"] == "heuristic"
    assert usage["route_usage"][0]["linked_endpoints"] == ["/api/todos"]


def test_frontend_bind_exemptions_default_path_warning_and_strict(tmp_path):
    contracts = tmp_path / "artifacts" / "contracts" / "latest"
    contracts.mkdir(parents=True)
    (contracts / "api_contract.json").write_text(
        json.dumps({"endpoints": [{"method": "GET", "path": "/api/todos"}]}),
        encoding="utf-8",
    )
    (contracts / "ROUTES.md").write_text("# ROUTES\n\n## /dashboard\n", encoding="utf-8")

    frontend = tmp_path / "frontend"
    (frontend / "src" / "dashboard").mkdir(parents=True)
    (frontend / "src" / "dashboard" / "page.tsx").write_text(
        "const mockTodos = [{id:1}]; fetch('/api/todos')\n",
        encoding="utf-8",
    )

    config_dir = tmp_path / "skills" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "exemptions.json").write_text(
        json.dumps(
            {
                "exemptions": [
                    {
                        "id": "EX-VALID-001",
                        "rule": "frontend_mock_data",
                        "path_or_pattern": "src/dashboard/*",
                        "reason": "temporary migration",
                        "owner": "frontend-team",
                        "expires_on": "2099-01-01",
                    },
                    {
                        "id": "EX-EXPIRED-001",
                        "rule": "frontend_route_unlinked",
                        "path_or_pattern": "/unused",
                        "reason": "old route",
                        "owner": "frontend-team",
                        "expires_on": "2000-01-01",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    base_args = dict(
        workspace_root=str(tmp_path),
        contracts_dir="artifacts/contracts/latest",
        frontend_root="frontend",
        allowlist_config=None,
        template=None,
        json=False,
    )

    code = frontend_bind.run(SimpleNamespace(**base_args, strict=False))
    assert code == 0
    gate_text = (tmp_path / "artifacts" / "frontend" / "latest" / "GateReport.md").read_text(encoding="utf-8")
    assert "expired on 2000-01-01" in gate_text
    mock_report = json.loads((tmp_path / "artifacts" / "frontend" / "latest" / "mock_data_report.json").read_text(encoding="utf-8"))
    assert mock_report["schema_version"] == "1.0"

    strict_code = frontend_bind.run(SimpleNamespace(**base_args, strict=True))
    assert strict_code == 1
