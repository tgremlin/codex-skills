from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swarm_skills.catalog import resolve_template
from swarm_skills.runtime import SkillRun, run_command, write_json


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
SOURCE_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".py"}
_PATH_PARAM_SEGMENT_RE = re.compile(r"^(\{[^}/]+\}|:[A-Za-z_][A-Za-z0-9_]*|<[^>/]+>|\[[^]/]+\])$")
SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str


def _default_template_id(workspace_root: Path) -> str | None:
    choice_path = workspace_root / "artifacts" / "plan" / "template_choice.json"
    if not choice_path.exists():
        return None
    try:
        raw = json.loads(choice_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    selected = raw.get("selected_template", {})
    template_id = selected.get("id")
    if isinstance(template_id, str) and template_id:
        return template_id
    return None


def _normalize_path(path: str) -> str:
    cleaned = path.strip()
    if not cleaned:
        return "/"
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    cleaned = cleaned.split("?", 1)[0].split("#", 1)[0]
    cleaned = re.sub(r"/+", "/", cleaned)
    if cleaned.endswith("/") and cleaned != "/":
        cleaned = cleaned.rstrip("/")
    return cleaned


def _normalize_method(method: str) -> str:
    return method.strip().upper()


def _normalize_param_path(path: str) -> str:
    normalized = _normalize_path(path)
    segments = [item for item in normalized.split("/") if item]
    mapped: list[str] = []
    for segment in segments:
        if _PATH_PARAM_SEGMENT_RE.match(segment):
            mapped.append("{param}")
        else:
            mapped.append(segment)
    return "/" + "/".join(mapped) if mapped else "/"


def _endpoint_key(method: str, path: str) -> tuple[str, str]:
    return _normalize_method(method), _normalize_path(path)


def _load_required_contracts(contract_path: Path) -> list[dict[str, Any]]:
    raw = json.loads(contract_path.read_text(encoding="utf-8"))
    endpoints = raw.get("endpoints", [])
    results: list[dict[str, Any]] = []
    for index, item in enumerate(endpoints, start=1):
        method = str(item.get("method", "")).upper()
        path = str(item.get("path", ""))
        if method not in HTTP_METHODS or not path:
            continue
        normalized_path = _normalize_path(path)
        results.append(
            {
                "auth": item.get("auth"),
                "method": _normalize_method(method),
                "normalized_path": _normalize_param_path(path),
                "path": normalized_path,
                "required": bool(item.get("required", True)),
                "request_schema": item.get("request_schema"),
                "response_schema": item.get("response_schema"),
                "source_id": item.get("id", f"CONTRACT-{index:03d}"),
            }
        )
    return sorted(results, key=lambda row: (row["path"], row["method"]))


def _discover_from_openapi(backend_root: Path) -> tuple[list[Endpoint], list[str]]:
    hints: list[str] = []
    endpoints: list[Endpoint] = []
    for candidate in [backend_root / "openapi.json", backend_root / "openapi.generated.json"]:
        if not candidate.exists():
            continue
        hints.append(str(candidate))
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        paths = data.get("paths", {})
        if not isinstance(paths, dict):
            continue
        for path, methods in sorted(paths.items()):
            if not isinstance(methods, dict):
                continue
            for method in sorted(methods.keys()):
                upper = method.upper()
                if upper in HTTP_METHODS:
                    endpoints.append(Endpoint(method=upper, path=path))
    return endpoints, hints


def _discover_from_static_scan(backend_root: Path) -> tuple[list[Endpoint], list[str]]:
    endpoints: set[tuple[str, str]] = set()
    hints: list[str] = []

    express_route = re.compile(r"\.([A-Za-z]+)\(\s*['\"]([^'\"]+)['\"]")
    fastapi_route = re.compile(r"@(?:app|router)\.([A-Za-z]+)\(\s*['\"]([^'\"]+)['\"]")
    node_compare = re.compile(
        r'req\.method\s*===\s*"([A-Z]+)"\s*&&\s*url\.pathname\s*===\s*"([^"]+)"'
    )
    node_starts = re.compile(
        r'req\.method\s*===\s*"([A-Z]+)"\s*&&\s*url\.pathname\.startsWith\(\s*"([^"]+)"\s*\)'
    )

    for path in sorted(backend_root.rglob("*")):
        if path.suffix.lower() not in SOURCE_EXTENSIONS or not path.is_file():
            continue
        rel = str(path.relative_to(backend_root))
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        local_hits = 0
        for pattern in [express_route, fastapi_route, node_compare, node_starts]:
            for match in pattern.finditer(content):
                method = match.group(1).upper()
                raw_path = match.group(2)
                if method not in HTTP_METHODS:
                    continue
                if pattern is node_starts and raw_path.endswith("/"):
                    raw_path = raw_path + ":id"
                endpoints.add(_endpoint_key(method, raw_path))
                local_hits += 1

        if local_hits > 0:
            hints.append(rel)

    endpoint_objects = [Endpoint(method=method, path=path) for method, path in sorted(endpoints)]
    return endpoint_objects, sorted(hints)


def _discover_from_template_command(template: Any) -> tuple[list[Endpoint], list[str], str | None]:
    inventory_cmd = template.boot.get("inventory_cmd")
    if not inventory_cmd:
        return [], [], None
    if not isinstance(inventory_cmd, list) or not inventory_cmd:
        return [], [], "Template inventory_cmd must be a non-empty command array."

    result = run_command(list(map(str, inventory_cmd)), cwd=template.path, timeout_sec=90)
    if result.exit_code != 0:
        stderr = result.stderr.strip()[-300:]
        return [], [f"inventory_cmd failed: {' '.join(result.cmd)}"], f"inventory_cmd failed with exit_code={result.exit_code}: {stderr}"

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [], [f"inventory_cmd: {' '.join(result.cmd)}"], "inventory_cmd output was not valid JSON."

    if not isinstance(payload, dict):
        return [], [f"inventory_cmd: {' '.join(result.cmd)}"], "inventory_cmd output must be an object."

    if "endpoints" not in payload:
        return [], [f"inventory_cmd: {' '.join(result.cmd)}"], "inventory_cmd output must include `endpoints` array."

    raw_endpoints = payload.get("endpoints", [])
    if not isinstance(raw_endpoints, list):
        return [], [f"inventory_cmd: {' '.join(result.cmd)}"], "inventory_cmd output must include `endpoints` array."

    endpoints: list[Endpoint] = []
    for index, row in enumerate(raw_endpoints):
        if not isinstance(row, dict):
            return [], [f"inventory_cmd: {' '.join(result.cmd)}"], f"inventory_cmd endpoints[{index}] must be an object."
        method = str(row.get("method", "")).upper()
        path = str(row.get("path", ""))
        if method in HTTP_METHODS and path:
            endpoints.append(Endpoint(method=_normalize_method(method), path=_normalize_path(path)))
        else:
            return [], [f"inventory_cmd: {' '.join(result.cmd)}"], f"inventory_cmd endpoints[{index}] must contain valid method/path."
    return sorted(endpoints, key=lambda item: (item.path, item.method)), [f"inventory_cmd: {' '.join(result.cmd)}"], None


def _path_family(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def _is_path_param_segment(segment: str) -> bool:
    if not segment:
        return False
    return bool(_PATH_PARAM_SEGMENT_RE.match(segment)) or segment.isdigit()


def _fuzzy_similarity(path_a: str, path_b: str) -> float:
    if path_a == path_b:
        return 1.0
    segments_a = [item for item in path_a.strip("/").split("/") if item]
    segments_b = [item for item in path_b.strip("/").split("/") if item]
    if not segments_a and not segments_b:
        return 1.0
    max_len = max(len(segments_a), len(segments_b))
    score = 0.0
    for index in range(max_len):
        left = segments_a[index] if index < len(segments_a) else ""
        right = segments_b[index] if index < len(segments_b) else ""
        if left == right and left:
            score += 1.0
            continue
        if _is_path_param_segment(left) or _is_path_param_segment(right):
            score += 0.8
            continue
        if left.rstrip("s") == right.rstrip("s") and left and right:
            score += 0.65
            continue
        if left and right and (left in right or right in left):
            score += 0.55
            continue
    return round(score / max_len, 3)


def _compute_coverage(
    contract_endpoints: list[dict[str, Any]],
    inventory: list[Endpoint],
) -> dict[str, Any]:
    inventory_rows: list[dict[str, Any]] = []
    for endpoint in inventory:
        method = _normalize_method(endpoint.method)
        path = _normalize_path(endpoint.path)
        inventory_rows.append(
            {
                "method": method,
                "normalized_path": _normalize_param_path(path),
                "path": path,
            }
        )

    inventory_exact: dict[tuple[str, str], dict[str, Any]] = {
        (row["method"], row["path"]): row for row in sorted(inventory_rows, key=lambda item: (item["path"], item["method"]))
    }
    inventory_normalized: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(inventory_rows, key=lambda item: (item["normalized_path"], item["method"], item["path"])):
        key = (row["method"], row["normalized_path"])
        inventory_normalized.setdefault(key, row)

    inventory_methods_by_path: dict[str, set[str]] = {}
    inventory_paths_by_method: dict[str, set[str]] = {}
    inventory_rows_by_method: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(inventory_rows, key=lambda item: (item["path"], item["method"])):
        inventory_methods_by_path.setdefault(row["path"], set()).add(row["method"])
        inventory_paths_by_method.setdefault(row["method"], set()).add(row["path"])
        inventory_rows_by_method.setdefault(row["method"], []).append(row)

    missing_required: list[dict[str, Any]] = []
    missing_optional: list[dict[str, Any]] = []
    mismatched_methods: list[dict[str, Any]] = []
    mismatched_paths: list[dict[str, Any]] = []
    required_fuzzy_matches: list[dict[str, Any]] = []
    endpoint_matches: list[dict[str, Any]] = []
    matched_inventory_keys: set[tuple[str, str]] = set()

    contract_keys_exact: set[tuple[str, str]] = set()
    contract_keys_normalized: set[tuple[str, str]] = set()
    for endpoint in contract_endpoints:
        key = (endpoint["method"], endpoint["path"])
        normalized_key = (endpoint["method"], endpoint["normalized_path"])
        contract_keys_exact.add(key)
        contract_keys_normalized.add(normalized_key)

        matched_row: dict[str, Any] | None = None
        match_type = "unmatched"
        confidence = 0.0

        exact_row = inventory_exact.get(key)
        if exact_row is not None:
            matched_row = exact_row
            match_type = "exact_match"
            confidence = 1.0
        else:
            normalized_row = inventory_normalized.get(normalized_key)
            if normalized_row is not None:
                matched_row = normalized_row
                match_type = "normalized_match"
                confidence = 0.9
            else:
                candidates = inventory_rows_by_method.get(endpoint["method"], [])
                candidate_ranked = sorted(
                    (
                        (
                            _fuzzy_similarity(endpoint["normalized_path"], candidate["normalized_path"]),
                            candidate,
                        )
                        for candidate in candidates
                    ),
                    key=lambda row: (-row[0], row[1]["path"]),
                )
                if candidate_ranked and candidate_ranked[0][0] >= 0.7:
                    matched_row = candidate_ranked[0][1]
                    match_type = "fuzzy_match"
                    confidence = 0.7

        match_record = {
            "confidence": confidence,
            "contract": {"method": endpoint["method"], "path": endpoint["path"]},
            "match_type": match_type,
            "matched_to": None,
            "required": endpoint["required"],
            "source_id": endpoint.get("source_id"),
        }
        if matched_row is not None:
            match_record["matched_to"] = {
                "method": matched_row["method"],
                "path": matched_row["path"],
            }
            matched_inventory_keys.add((matched_row["method"], matched_row["path"]))
            if endpoint["required"] and match_type == "fuzzy_match":
                required_fuzzy_matches.append(
                    {
                        "confidence": confidence,
                        "method": endpoint["method"],
                        "path": endpoint["path"],
                        "source_id": endpoint.get("source_id"),
                    }
                )
        endpoint_matches.append(match_record)

        if matched_row is not None:
            continue

        row = {
            "method": endpoint["method"],
            "path": endpoint["path"],
            "source_id": endpoint.get("source_id"),
        }
        if endpoint["required"]:
            missing_required.append(row)
        else:
            missing_optional.append(row)

        methods_for_path = inventory_methods_by_path.get(endpoint["path"], set())
        if methods_for_path:
            mismatched_methods.append(
                {
                    "path": endpoint["path"],
                    "required_method": endpoint["method"],
                    "available_methods": sorted(methods_for_path),
                }
            )

        same_method_paths = inventory_paths_by_method.get(endpoint["method"], set())
        family = _path_family(endpoint["normalized_path"])
        candidates = [
            item
            for item in sorted(same_method_paths)
            if _path_family(_normalize_param_path(item)) == family and item != endpoint["path"]
        ]
        if candidates:
            mismatched_paths.append(
                {
                    "method": endpoint["method"],
                    "required_path": endpoint["path"],
                    "available_paths": candidates,
                }
            )

    extra_endpoints = []
    for method, path in sorted(inventory_exact):
        if (method, path) in matched_inventory_keys:
            continue
        if (method, path) in contract_keys_exact:
            continue
        if (method, _normalize_param_path(path)) in contract_keys_normalized:
            continue
        extra_endpoints.append({"method": method, "path": path})

    return {
        "schema_version": SCHEMA_VERSION,
        "contract_endpoint_matches": sorted(
            endpoint_matches,
            key=lambda row: (row["contract"]["path"], row["contract"]["method"], str(row.get("source_id", ""))),
        ),
        "extra_endpoints": extra_endpoints,
        "missing_optional": sorted(missing_optional, key=lambda row: (row["path"], row["method"])),
        "missing_required": sorted(missing_required, key=lambda row: (row["path"], row["method"])),
        "mismatched_methods": sorted(mismatched_methods, key=lambda row: (row["path"], row["required_method"])),
        "mismatched_paths": sorted(mismatched_paths, key=lambda row: (row["required_path"], row["method"])),
        "required_fuzzy_matches": sorted(required_fuzzy_matches, key=lambda row: (row["path"], row["method"])),
    }


def _resolve_backend_root(args: Any, workspace_root: Path, template: Any | None) -> Path:
    if args.backend_root:
        return (workspace_root / args.backend_root).resolve()
    if template is not None:
        return template.path.resolve()
    return workspace_root.resolve()


def run(args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    skill_run = SkillRun(skill="backend_build", workspace_root=workspace_root, artifact_dir_name="backend")

    template = None
    template_id = args.template
    if not template_id:
        template_id = _default_template_id(workspace_root)
    if template_id:
        try:
            template = resolve_template(template_id, workspace_root)
        except FileNotFoundError:
            template = None

    contract_path = (workspace_root / args.contracts).resolve() if args.contracts else (workspace_root / "artifacts" / "contracts" / "latest" / "api_contract.json")
    if not contract_path.exists():
        gate_report = skill_run.run_dir / "GateReport.md"
        gate_report.write_text(
            "# Backend GateReport\n\nStatus: FAIL\n\nContract file not found.\n\nNext fix steps:\n1. Run `python -m skills plan_to_contracts --spec <SPEC.md>` first.\n2. Re-run `python -m skills backend_build`.\n",
            encoding="utf-8",
        )
        skill_run.record_artifact(gate_report)
        skill_run.add_note(f"Missing contract file: {contract_path}")
        return skill_run.finalize(
            "fail",
            emit_json=args.json,
            provenance={"template_id": getattr(template, "id", None), "template_version": getattr(template, "version", None)},
        )

    try:
        contract_endpoints = _load_required_contracts(contract_path)
    except json.JSONDecodeError as exc:
        gate_report = skill_run.run_dir / "GateReport.md"
        gate_report.write_text(
            f"# Backend GateReport\n\nStatus: FAIL\n\nContract JSON is invalid: {exc}\n",
            encoding="utf-8",
        )
        skill_run.record_artifact(gate_report)
        skill_run.add_note("Contract JSON parsing failed.")
        return skill_run.finalize(
            "fail",
            emit_json=args.json,
            provenance={"template_id": getattr(template, "id", None), "template_version": getattr(template, "version", None)},
        )

    backend_root = _resolve_backend_root(args, workspace_root, template)
    strategy_logs: list[dict[str, Any]] = []

    template_inventory: list[Endpoint] = []
    template_hints: list[str] = []
    template_error: str | None = None
    if template is not None:
        template_inventory, template_hints, template_error = _discover_from_template_command(template)
    template_inventory_declared = bool(template is not None and template.boot.get("inventory_cmd"))
    strategy_logs.append(
        {
            "strategy": "template_inventory_cmd",
            "count": len(template_inventory),
            "hints": template_hints,
            "error": template_error,
            "preferred": template_inventory_declared,
        }
    )

    if template_inventory_declared and template_error:
        gate_report_path = skill_run.run_dir / "GateReport.md"
        gate_report_path.write_text(
            "\n".join(
                [
                    "# Backend GateReport",
                    "",
                    "Status: FAIL",
                    "",
                    f"Template inventory command output is invalid: {template_error}",
                    "",
                    "Next fix steps:",
                    "1. Update template `boot.inventory_cmd` to emit JSON schema `{ \"endpoints\": [{\"method\":\"GET\",\"path\":\"/api/x/{param}\"}] }`.",
                    "2. Re-run the inventory command directly to verify output.",
                    "3. Re-run `python -m skills backend_build`.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        skill_run.record_artifact(gate_report_path)
        skill_run.add_note("inventory_cmd schema validation failed.")
        return skill_run.finalize(
            "fail",
            emit_json=args.json,
            provenance={"template_id": getattr(template, "id", None), "template_version": getattr(template, "version", None)},
        )

    static_hints: list[str] = []
    if template_inventory_declared:
        strategy_logs.append({"strategy": "openapi", "count": 0, "hints": ["skipped: inventory_cmd preferred"]})
        strategy_logs.append({"strategy": "static_scan", "count": 0, "hints": ["skipped: inventory_cmd preferred"]})
        inventory = template_inventory
    else:
        openapi_inventory, openapi_hints = _discover_from_openapi(backend_root)
        strategy_logs.append({"strategy": "openapi", "count": len(openapi_inventory), "hints": openapi_hints})

        static_inventory, static_hints = _discover_from_static_scan(backend_root)
        strategy_logs.append({"strategy": "static_scan", "count": len(static_inventory), "hints": static_hints[:20]})

        merged: dict[tuple[str, str], Endpoint] = {}
        for source in [openapi_inventory, static_inventory]:
            for endpoint in source:
                merged[(_normalize_method(endpoint.method), _normalize_path(endpoint.path))] = Endpoint(
                    method=_normalize_method(endpoint.method),
                    path=_normalize_path(endpoint.path),
                )
        inventory = [merged[key] for key in sorted(merged)]

    api_inventory = {
        "backend_root": str(backend_root.relative_to(workspace_root)) if backend_root.is_relative_to(workspace_root) else str(backend_root),
        "contract_path": str(contract_path.relative_to(workspace_root)) if contract_path.is_relative_to(workspace_root) else str(contract_path),
        "discovery_strategies": strategy_logs,
        "endpoints": [{"method": item.method, "path": item.path} for item in inventory],
    }

    coverage = _compute_coverage(contract_endpoints, inventory)
    missing_required = coverage["missing_required"]
    missing_optional = coverage["missing_optional"]
    fuzzy_required = coverage.get("required_fuzzy_matches", [])

    status = "fail" if missing_required else "pass"
    if missing_optional and not missing_required:
        skill_run.add_note("Assumption: optional endpoints may be intentionally deferred.")
    if fuzzy_required:
        skill_run.add_note("Required endpoints matched fuzzily; review path naming consistency.")

    api_inventory_path = skill_run.run_dir / "api_inventory.json"
    coverage_path = skill_run.run_dir / "contract_coverage.json"
    gate_report_path = skill_run.run_dir / "GateReport.md"

    write_json(api_inventory_path, api_inventory)
    write_json(coverage_path, coverage)

    lines = ["# Backend GateReport", ""]
    match_counts = {"exact_match": 0, "normalized_match": 0, "fuzzy_match": 0, "unmatched": 0}
    for row in coverage.get("contract_endpoint_matches", []):
        match_counts[str(row.get("match_type", "unmatched"))] = match_counts.get(str(row.get("match_type", "unmatched")), 0) + 1

    if status == "pass":
        lines.extend([
            "Status: PASS",
            "",
            f"Required missing endpoints: {len(missing_required)}",
            f"Optional missing endpoints: {len(missing_optional)}",
            f"Exact matches: {match_counts.get('exact_match', 0)}",
            f"Normalized matches: {match_counts.get('normalized_match', 0)}",
            f"Fuzzy matches: {match_counts.get('fuzzy_match', 0)}",
        ])
        if missing_optional:
            lines.extend(["", "Warnings:"])
            for row in missing_optional:
                lines.append(f"- Optional endpoint not implemented: `{row['method']} {row['path']}`")
        if fuzzy_required:
            lines.extend(["", "Warnings: required endpoints matched with fuzzy confidence:"])
            for row in fuzzy_required:
                lines.append(
                    f"- `{row['method']} {row['path']}` (contract id: {row.get('source_id')}, confidence={row['confidence']})"
                )
        lines.extend([
            "",
            "Next fix steps:",
            "1. Keep coverage in sync whenever contracts change.",
            "2. Re-run backend_build after backend route changes.",
        ])
    else:
        lines.extend(
            [
                "Status: FAIL",
                "",
                f"Exact matches: {match_counts.get('exact_match', 0)}",
                f"Normalized matches: {match_counts.get('normalized_match', 0)}",
                f"Fuzzy matches: {match_counts.get('fuzzy_match', 0)}",
                "",
                "Missing required endpoints:",
            ]
        )
        for row in missing_required:
            lines.append(f"- `{row['method']} {row['path']}` (contract id: {row.get('source_id')})")
        if fuzzy_required:
            lines.extend(["", "Required endpoints only fuzzy-matched (review):"])
            for row in fuzzy_required:
                lines.append(
                    f"- `{row['method']} {row['path']}` (contract id: {row.get('source_id')}, confidence={row['confidence']})"
                )
        lines.extend(["", "Likely files to inspect:"])
        likely_files = static_hints[:8]
        if not likely_files:
            likely_files = ["templates/local-node-http-crud/server.js"]
        for hint in likely_files:
            lines.append(f"- `{hint}`")
        lines.extend([
            "",
            "Next fix steps:",
            "1. Implement the missing required routes in backend source files listed above.",
            "2. If route signatures changed intentionally, update `artifacts/contracts/latest/api_contract.json` via S3 and re-run.",
            "3. Re-run `python -m skills backend_build`.",
        ])

    gate_report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for path in [api_inventory_path, coverage_path, gate_report_path]:
        skill_run.record_artifact(path)

    if status == "fail":
        skill_run.add_note("Backend coverage gate failed.")
    else:
        skill_run.add_note("Backend coverage gate passed.")

    return skill_run.finalize(
        status,
        emit_json=args.json,
        provenance={"template_id": getattr(template, "id", None), "template_version": getattr(template, "version", None)},
    )
