from __future__ import annotations

import fnmatch
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from swarm_skills.catalog import resolve_template
from swarm_skills.runtime import SkillRun, write_json

SOURCE_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".html", ".mdx"}
API_CLIENT_CANDIDATES = [
    "lib/apiClient.ts",
    "lib/api.ts",
    "src/lib/apiClient.ts",
    "src/lib/api.ts",
]
EXEMPTION_REQUIRED_FIELDS = {"id", "rule", "path_or_pattern", "reason", "owner", "expires_on"}
SCHEMA_VERSION = "1.0"
DEFAULT_MOCK_PATTERNS = [
    r"\bmock\b",
    r"\bfixtures?\b",
    r"\bfake[A-Z_a-z0-9]*\b",
    r"const\s+[A-Za-z0-9_]+\s*=\s*\[\s*\{",
    r"const\s+[A-Za-z0-9_]+\s*=\s*\{\s*\"[A-Za-z0-9_\-]+\"\s*:",
]


def _default_template_id(workspace_root: Path) -> str | None:
    choice = workspace_root / "artifacts" / "plan" / "template_choice.json"
    if not choice.exists():
        return None
    try:
        raw = json.loads(choice.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    selected = raw.get("selected_template", {})
    template_id = selected.get("id")
    if isinstance(template_id, str) and template_id:
        return template_id
    return None


def _default_allowlist_payload() -> dict[str, Any]:
    return {
        "endpoint_exempt_patterns": [],
        "endpoint_exemptions": [],
        "errors": [],
        "expired_exemptions": [],
        "mock_exempt_paths": [],
        "mock_exempt_patterns": [],
        "mock_patterns": DEFAULT_MOCK_PATTERNS,
        "route_exempt_patterns": [],
        "route_exemptions": {},
        "source": None,
        "warnings": [],
    }


def _is_expired(expires_on: str, today: date) -> bool:
    parsed = datetime.strptime(expires_on, "%Y-%m-%d").date()
    return parsed < today


def _load_allowlist(path: Path | None) -> dict[str, Any]:
    payload = _default_allowlist_payload()
    if path is None:
        return payload
    if not path.exists():
        return payload

    raw = json.loads(path.read_text(encoding="utf-8"))
    payload["source"] = str(path)

    # New schema: {"exemptions": [...]}
    if "exemptions" in raw:
        exemptions = raw.get("exemptions")
        if not isinstance(exemptions, list):
            payload["errors"].append("`exemptions` must be a list.")
            return payload

        today = date.today()
        for index, item in enumerate(exemptions):
            if not isinstance(item, dict):
                payload["errors"].append(f"Exemption[{index}] must be an object.")
                continue

            missing = sorted(EXEMPTION_REQUIRED_FIELDS - set(item.keys()))
            if missing:
                payload["errors"].append(
                    f"Exemption[{index}] missing required fields: {', '.join(missing)}"
                )
                continue

            exemption_id = str(item.get("id"))
            rule = str(item.get("rule"))
            pattern = str(item.get("path_or_pattern"))
            expires_on = str(item.get("expires_on"))
            try:
                expired = _is_expired(expires_on, today)
            except ValueError:
                payload["errors"].append(
                    f"Exemption `{exemption_id}` has invalid expires_on `{expires_on}`; expected YYYY-MM-DD."
                )
                continue

            if expired:
                payload["expired_exemptions"].append(exemption_id)
                payload["warnings"].append(
                    f"Exemption `{exemption_id}` expired on {expires_on} and was not applied."
                )
                continue

            if rule == "frontend_mock_data":
                payload["mock_exempt_patterns"].append(pattern)
            elif rule == "frontend_endpoint_not_in_contract":
                payload["endpoint_exempt_patterns"].append(pattern)
            elif rule == "frontend_route_unlinked":
                payload["route_exempt_patterns"].append(pattern)
            else:
                payload["warnings"].append(
                    f"Exemption `{exemption_id}` uses unsupported rule `{rule}`; ignored."
                )

        for key in [
            "endpoint_exempt_patterns",
            "mock_exempt_patterns",
            "route_exempt_patterns",
        ]:
            payload[key] = sorted(set(map(str, payload[key])))
        return payload

    # Legacy schema compatibility.
    payload["endpoint_exemptions"] = sorted(set(map(str, raw.get("endpoint_exemptions", []))))
    payload["mock_exempt_paths"] = sorted(set(map(str, raw.get("mock_exempt_paths", []))))
    payload["mock_patterns"] = list(raw.get("mock_patterns", DEFAULT_MOCK_PATTERNS))
    payload["route_exemptions"] = dict(raw.get("route_exemptions", {}))
    return payload


def _load_contract_endpoints(contracts_dir: Path) -> set[str]:
    contract = contracts_dir / "api_contract.json"
    if not contract.exists():
        return set()
    raw = json.loads(contract.read_text(encoding="utf-8"))
    endpoints = raw.get("endpoints", [])
    values = {str(item.get("path", "")).strip() for item in endpoints if item.get("path")}
    return {value.rstrip("/") if value != "/" else value for value in values}


def _load_routes(contracts_dir: Path) -> list[str]:
    routes_path = contracts_dir / "ROUTES.md"
    if not routes_path.exists():
        return []
    routes: list[str] = []
    for line in routes_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^##\s+(/[^\s]*)\s*$", line.strip())
        if match:
            routes.append(match.group(1))
    return sorted(set(routes))


def _iter_source_files(frontend_root: Path) -> list[Path]:
    results: list[Path] = []
    for path in sorted(frontend_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        rel = str(path.relative_to(frontend_root))
        lowered = rel.lower()
        if lowered.startswith("tests/") or "/tests/" in lowered or "__tests__/" in lowered:
            continue
        if any(token in lowered for token in [".spec.", ".test."]):
            continue
        results.append(path)
    return results


def _find_api_client_files(frontend_root: Path) -> list[Path]:
    found: list[Path] = []
    for rel in API_CLIENT_CANDIDATES:
        candidate = frontend_root / rel
        if candidate.exists() and candidate.is_file():
            found.append(candidate)
    return found


def _extract_api_client_methods(content: str) -> dict[str, set[str]]:
    method_positions: list[tuple[int, str]] = []
    patterns = [
        re.compile(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
        re.compile(r"(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"),
        re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:async\s*)?\([^)]*\)\s*=>"),
    ]
    for regex in patterns:
        for match in regex.finditer(content):
            method_positions.append((match.start(), match.group(1)))
    method_positions = sorted(set(method_positions), key=lambda row: (row[0], row[1]))
    if not method_positions:
        endpoints = _extract_endpoints(content)
        return {"__all__": set(endpoints)} if endpoints else {}

    method_to_endpoints: dict[str, set[str]] = {}
    for index, (start, method_name) in enumerate(method_positions):
        end = method_positions[index + 1][0] if index + 1 < len(method_positions) else len(content)
        block = content[start:end]
        endpoints = _extract_endpoints(block)
        if endpoints:
            method_to_endpoints.setdefault(method_name, set()).update(endpoints)

    if not method_to_endpoints:
        endpoints = _extract_endpoints(content)
        if endpoints:
            method_to_endpoints["__all__"] = set(endpoints)
    return method_to_endpoints


def _has_api_client_import(content: str) -> bool:
    for token in ["lib/apiClient", "lib/api", "src/lib/apiClient", "src/lib/api"]:
        if token in content:
            return True
    return False


def _extract_endpoints_via_api_client(content: str, method_to_endpoints: dict[str, set[str]]) -> list[str]:
    if not method_to_endpoints or not _has_api_client_import(content):
        return []
    endpoints: set[str] = set()
    for method_name, values in method_to_endpoints.items():
        if method_name == "__all__":
            continue
        if re.search(rf"\b{re.escape(method_name)}\s*\(", content):
            endpoints.update(values)
    if not endpoints and "__all__" in method_to_endpoints:
        endpoints.update(method_to_endpoints["__all__"])
    return sorted(endpoints)


def _extract_endpoints(content: str) -> list[str]:
    found: set[str] = set()
    for match in re.finditer(r"['\"](/api/[A-Za-z0-9_\-/{}/:.]*)['\"]", content):
        endpoint = match.group(1).split("?", 1)[0].rstrip("/")
        if not endpoint:
            endpoint = "/"
        found.add(endpoint)
    return sorted(found)


def _route_candidates(route: str, frontend_root: Path, path: Path, content: str) -> bool:
    rel = str(path.relative_to(frontend_root)).lower()
    if route == "/":
        return any(token in rel for token in ["index", "page", "home", "server"])
    route_slug = route.strip("/").replace("/", "-").lower()
    return route_slug in rel or route in content


def _matches_patterns(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def _scan_mock_data(
    frontend_root: Path,
    files: list[Path],
    allowlist: dict[str, Any],
) -> list[dict[str, Any]]:
    regexes = [re.compile(pattern, flags=re.IGNORECASE) for pattern in allowlist["mock_patterns"]]
    exempt = set(allowlist["mock_exempt_paths"])
    exempt_patterns = list(allowlist.get("mock_exempt_patterns", []))
    findings: list[dict[str, Any]] = []
    for path in files:
        rel = str(path.relative_to(frontend_root))
        if rel in exempt or _matches_patterns(rel, exempt_patterns):
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        for regex in regexes:
            match = regex.search(content)
            if match:
                findings.append(
                    {
                        "file": rel,
                        "pattern": regex.pattern,
                        "excerpt": match.group(0)[:120],
                    }
                )
                break
    return sorted(findings, key=lambda row: row["file"])


def run(args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    skill_run = SkillRun(skill="frontend_bind", workspace_root=workspace_root, artifact_dir_name="frontend")

    contracts_dir = (workspace_root / args.contracts_dir).resolve() if args.contracts_dir else (workspace_root / "artifacts" / "contracts" / "latest")
    if not contracts_dir.exists():
        gate_report = skill_run.run_dir / "GateReport.md"
        gate_report.write_text(
            "# Frontend Bind GateReport\n\nStatus: FAIL\n\nContracts directory not found.\n",
            encoding="utf-8",
        )
        skill_run.record_artifact(gate_report)
        skill_run.add_note(f"Missing contracts directory: {contracts_dir}")
        return skill_run.finalize("fail", emit_json=args.json)

    template_id = args.template
    template = None
    if not template_id:
        template_id = _default_template_id(workspace_root)
    if template_id:
        try:
            template = resolve_template(template_id, workspace_root)
        except FileNotFoundError:
            template = None

    if args.frontend_root:
        frontend_root = (workspace_root / args.frontend_root).resolve()
    elif template is not None:
        frontend_root = template.path.resolve()
    else:
        frontend_root = workspace_root.resolve()

    allowlist_path = (workspace_root / args.allowlist_config).resolve() if args.allowlist_config else None
    if allowlist_path is None:
        default_exemptions = workspace_root / "skills" / "config" / "exemptions.json"
        if default_exemptions.exists():
            allowlist_path = default_exemptions
    allowlist = _load_allowlist(allowlist_path)
    strict_mode = bool(getattr(args, "strict", False))

    contract_endpoints = _load_contract_endpoints(contracts_dir)
    critical_routes = _load_routes(contracts_dir)

    files = _iter_source_files(frontend_root)
    api_client_files = _find_api_client_files(frontend_root)
    api_client_methods: dict[str, set[str]] = {}
    for api_client_file in api_client_files:
        content = api_client_file.read_text(encoding="utf-8", errors="ignore")
        extracted = _extract_api_client_methods(content)
        for method_name, endpoints in extracted.items():
            api_client_methods.setdefault(method_name, set()).update(endpoints)

    file_endpoints: dict[str, list[str]] = {}
    file_text: dict[str, str] = {}
    for file_path in files:
        rel = str(file_path.relative_to(frontend_root))
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        file_text[rel] = content
        api_client_endpoints = _extract_endpoints_via_api_client(content, api_client_methods)
        heuristic_endpoints = _extract_endpoints(content)
        file_endpoints[rel] = api_client_endpoints if api_client_endpoints else heuristic_endpoints

    api_usage: dict[str, Any] = {
        "api_client_files": sorted(
            str(path.relative_to(frontend_root)) for path in api_client_files
        ),
        "contracts_dir": str(contracts_dir.relative_to(workspace_root)) if contracts_dir.is_relative_to(workspace_root) else str(contracts_dir),
        "detection_strategy": "api_client_preferred" if api_client_files else "heuristic",
        "frontend_root": str(frontend_root.relative_to(workspace_root)) if frontend_root.is_relative_to(workspace_root) else str(frontend_root),
        "route_usage": [],
        "schema_version": SCHEMA_VERSION,
    }

    route_failures: list[str] = []
    endpoint_failures: list[str] = []
    endpoint_exemptions = set(allowlist["endpoint_exemptions"])
    endpoint_exempt_patterns = list(allowlist.get("endpoint_exempt_patterns", []))
    route_exemptions = set(map(str, allowlist.get("route_exemptions", {}).keys()))
    route_exempt_patterns = list(allowlist.get("route_exempt_patterns", []))

    for route in critical_routes:
        entries: list[dict[str, Any]] = []
        used_endpoints: set[str] = set()
        for rel, endpoints in sorted(file_endpoints.items()):
            content = file_text[rel]
            if not _route_candidates(route, frontend_root, frontend_root / rel, content):
                continue
            if endpoints:
                entries.append({"file": rel, "endpoints": endpoints})
                used_endpoints.update(endpoints)

        route_is_exempt = route in route_exemptions or _matches_patterns(route, route_exempt_patterns)
        if not entries and not route_is_exempt:
            route_failures.append(route)

        for endpoint in sorted(used_endpoints):
            normalized = endpoint.rstrip("/") if endpoint != "/" else endpoint
            exempted = (
                normalized in endpoint_exemptions
                or _matches_patterns(normalized, endpoint_exempt_patterns)
            )
            if contract_endpoints and normalized not in contract_endpoints and not exempted:
                endpoint_failures.append(f"{route} -> {normalized}")

        api_usage["route_usage"].append(
            {
                "route": route,
                "files": entries,
                "linked_endpoints": sorted(used_endpoints),
            }
        )

    mock_findings = _scan_mock_data(frontend_root, files, allowlist)
    mock_report = {
        "allowlist": allowlist,
        "findings": mock_findings,
        "schema_version": SCHEMA_VERSION,
        "status": "fail" if mock_findings else "pass",
    }

    status = "pass"
    config_errors = list(allowlist.get("errors", []))
    expired_exemptions = list(allowlist.get("expired_exemptions", []))
    if route_failures or endpoint_failures or mock_findings or config_errors:
        status = "fail"
    if strict_mode and expired_exemptions:
        status = "fail"

    api_usage_path = skill_run.run_dir / "api_usage.json"
    mock_report_path = skill_run.run_dir / "mock_data_report.json"
    gate_report_path = skill_run.run_dir / "GateReport.md"

    write_json(api_usage_path, api_usage)
    write_json(mock_report_path, mock_report)

    lines = ["# Frontend Bind GateReport", ""]
    if status == "pass":
        lines.extend(
            [
                "Status: PASS",
                "",
                f"Critical routes checked: {len(critical_routes)}",
                f"Mock data findings: {len(mock_findings)}",
            ]
        )
    else:
        lines.extend(["Status: FAIL", ""])
        if config_errors:
            lines.append("Exemption config errors:")
            for error in config_errors:
                lines.append(f"- {error}")
            lines.append("")
        if route_failures:
            lines.append("Routes with zero linked endpoints:")
            for route in sorted(route_failures):
                lines.append(f"- `{route}`")
            lines.append("")
        if endpoint_failures:
            lines.append("Linked endpoints not in contract:")
            for row in sorted(endpoint_failures):
                lines.append(f"- `{row}`")
            lines.append("")
        if mock_findings:
            lines.append("Mock data findings outside tests:")
            for finding in mock_findings[:20]:
                lines.append(f"- `{finding['file']}` matched `{finding['pattern']}`")
            lines.append("")
    if allowlist.get("warnings"):
        lines.append("Warnings:")
        for warning in sorted(set(map(str, allowlist["warnings"]))):
            lines.append(f"- {warning}")
        lines.append("")
    if strict_mode and expired_exemptions:
        lines.append("Strict mode failure:")
        for item in sorted(set(map(str, expired_exemptions))):
            lines.append(f"- expired exemption `{item}`")
        lines.append("")

    candidate_files = sorted({item["file"] for route in api_usage["route_usage"] for item in route["files"]})
    if candidate_files:
        lines.append("Candidate files:")
        for rel in candidate_files[:12]:
            lines.append(f"- `{rel}`")
        lines.append("")

    if status == "fail":
        lines.extend(
            [
                "Next fix steps:",
                "1. Replace runtime mock data with real API client calls for failing routes.",
                "2. Ensure linked endpoints are present in `artifacts/contracts/latest/api_contract.json` or add explicit exemptions.",
                "3. Re-run `python -m skills frontend_bind`.",
            ]
        )
    else:
        lines.extend(
            [
                "Next fix steps:",
                "1. Keep route-to-endpoint bindings aligned with contracts.",
                "2. Re-run `python -m skills frontend_bind` after frontend API changes.",
            ]
        )

    gate_report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for path in [api_usage_path, mock_report_path, gate_report_path]:
        skill_run.record_artifact(path)

    if config_errors:
        skill_run.add_note("Exemptions config has schema errors.")
    if strict_mode and expired_exemptions:
        skill_run.add_note("Strict mode failed due to expired exemptions.")
    if status == "fail":
        skill_run.add_note("Frontend binding gate failed.")
    else:
        skill_run.add_note("Frontend binding gate passed.")

    return skill_run.finalize(
        status,
        emit_json=args.json,
        provenance={"template_id": getattr(template, "id", None), "template_version": getattr(template, "version", None)},
    )
