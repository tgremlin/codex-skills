from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from swarm_skills.runtime import SkillRun, copy_or_replace_dir, write_json

_ALLOWED_LAYERS = {"ui", "api", "db"}
SCHEMA_VERSION = "1.0"


def _normalize_line(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def parse_acceptance_criteria(spec_text: str) -> list[dict[str, str]]:
    lines = spec_text.splitlines()
    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if re.match(r"^\s*#+\s*Acceptance Criteria\s*$", line, flags=re.IGNORECASE):
            start_idx = idx + 1
            break
        if re.match(r"^\s*Acceptance Criteria\s*:\s*$", line, flags=re.IGNORECASE):
            start_idx = idx + 1
            break

    if start_idx is None:
        return []

    criteria: list[dict[str, str]] = []
    for line in lines[start_idx:]:
        if re.match(r"^\s*#+\s+", line):
            break
        bullet = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+)$", line)
        if bullet:
            text = _normalize_line(bullet.group(1))
            if text:
                criteria.append(
                    {
                        "id": f"AC-{len(criteria)+1:03d}",
                        "text": text,
                    }
                )
    return criteria


def _infer_core_objects(spec_text: str) -> tuple[str, str, list[dict[str, str]]]:
    lowered = spec_text.lower()
    if "todo" in lowered:
        entity_name = "Todo"
        route = "/"
        fields = [
            {"name": "id", "type": "integer", "constraints": "required, unique"},
            {"name": "title", "type": "string", "constraints": "required"},
            {"name": "completed", "type": "boolean", "constraints": "required, default=false"},
        ]
    else:
        entity_name = "Item"
        route = "/"
        fields = [
            {"name": "id", "type": "integer", "constraints": "required, unique"},
            {"name": "name", "type": "string", "constraints": "required"},
            {"name": "status", "type": "string", "constraints": "required, default='new'"},
        ]
    return entity_name, route, fields


def _infer_endpoints(criteria: list[dict[str, str]], entity_name: str) -> list[dict[str, Any]]:
    resource = entity_name.lower() + "s"
    endpoints: list[dict[str, Any]] = [
        {
            "id": "EP-HEALTH-001",
            "method": "GET",
            "path": "/api/health",
            "auth": "none",
            "request_schema": "none",
            "response_schema": {"ok": "boolean"},
            "errors": [],
        }
    ]

    joined = " ".join(item["text"].lower() for item in criteria)
    wants_create = any(token in joined for token in ["create", "add"]) or len(criteria) > 0
    wants_list = any(token in joined for token in ["list", "view", "show"]) or len(criteria) > 0
    wants_update = any(token in joined for token in ["update", "edit", "mark", "complete"]) or len(criteria) > 0
    wants_delete = any(token in joined for token in ["delete", "remove"]) or len(criteria) > 0

    if wants_list:
        endpoints.append(
            {
                "id": "EP-LIST-001",
                "method": "GET",
                "path": f"/api/{resource}",
                "auth": "none",
                "request_schema": "none",
                "response_schema": [{"id": "integer", "title": "string", "completed": "boolean"}],
                "errors": [],
            }
        )
    if wants_create:
        endpoints.append(
            {
                "id": "EP-CREATE-001",
                "method": "POST",
                "path": f"/api/{resource}",
                "auth": "none",
                "request_schema": {"title": "string"},
                "response_schema": {"id": "integer", "title": "string", "completed": "boolean"},
                "errors": ["400 invalid payload"],
            }
        )
    if wants_update:
        endpoints.append(
            {
                "id": "EP-UPDATE-001",
                "method": "PUT",
                "path": f"/api/{resource}/{{id}}",
                "auth": "none",
                "request_schema": {"title": "string?", "completed": "boolean?"},
                "response_schema": {"id": "integer", "title": "string", "completed": "boolean"},
                "errors": ["404 not found", "400 invalid payload"],
            }
        )
    if wants_delete:
        endpoints.append(
            {
                "id": "EP-DELETE-001",
                "method": "DELETE",
                "path": f"/api/{resource}/{{id}}",
                "auth": "none",
                "request_schema": "none",
                "response_schema": {"deleted": "integer"},
                "errors": ["404 not found"],
            }
        )

    return sorted(endpoints, key=lambda row: (row["path"], row["method"]))


def _generate_test_plan_rows(criteria: list[dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in criteria:
        ac_id = item["id"]
        rows.append(
            {
                "test_id": f"TC-{ac_id[3:]}-UI",
                "acceptance_ids": ac_id,
                "layers": "ui",
                "description": f"UI flow covers: {item['text']}",
            }
        )
        rows.append(
            {
                "test_id": f"TC-{ac_id[3:]}-API",
                "acceptance_ids": ac_id,
                "layers": "api",
                "description": f"API checks cover: {item['text']}",
            }
        )
        rows.append(
            {
                "test_id": f"TC-{ac_id[3:]}-DB",
                "acceptance_ids": ac_id,
                "layers": "db",
                "description": f"DB assertions cover: {item['text']}",
            }
        )
    return rows


def _render_api_contract_markdown(endpoints: list[dict[str, Any]]) -> str:
    lines = ["# API_CONTRACT", ""]
    for endpoint in endpoints:
        lines.extend(
            [
                f"## {endpoint['id']} {endpoint['method']} {endpoint['path']}",
                f"- auth: `{endpoint['auth']}`",
                f"- request_schema: `{endpoint['request_schema']}`",
                f"- response_schema: `{endpoint['response_schema']}`",
                f"- errors: `{endpoint['errors']}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _render_data_model_markdown(entity_name: str, fields: list[dict[str, str]]) -> str:
    lines = [
        "# DATA_MODEL",
        "",
        f"## Entity: {entity_name}",
        "",
        "| field | type | constraints |",
        "|---|---|---|",
    ]
    for field in fields:
        lines.append(f"| {field['name']} | {field['type']} | {field['constraints']} |")
    lines.append("")
    return "\n".join(lines)


def _render_routes_markdown(route: str, endpoints: list[dict[str, Any]]) -> str:
    lines = [
        "# ROUTES",
        "",
        f"## {route}",
        "- key_view: list + create form",
        "- critical_actions: create, update, delete",
        "- api_calls:",
    ]
    for endpoint in endpoints:
        lines.append(f"  - {endpoint['method']} {endpoint['path']}")
    lines.extend(["", "## /health", "- key_view: health probe endpoint", ""])
    return "\n".join(lines)


def _render_test_plan_markdown(rows: list[dict[str, str]]) -> str:
    lines = [
        "# TEST_PLAN",
        "",
        "| test_id | acceptance_ids | layers | description |",
        "|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['test_id']} | {row['acceptance_ids']} | {row['layers']} | {row['description']} |"
        )
    lines.append("")
    return "\n".join(lines)


def parse_test_plan_markdown(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        if "test_id" in line or "---" in line:
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) != 4:
            continue
        test_id, acceptance_ids, layers, description = parts
        if not test_id:
            continue
        accepted = [item.strip() for item in acceptance_ids.split(",") if item.strip()]
        layer_values = [item.strip() for item in layers.split(",") if item.strip()]
        rows.append(
            {
                "test_id": test_id,
                "acceptance_ids": accepted,
                "layers": layer_values,
                "description": description,
            }
        )
    return rows


def _validate_mapping(
    criteria: list[dict[str, str]],
    parsed_tests: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not criteria:
        errors.append("No acceptance criteria were found under an 'Acceptance Criteria' section.")

    mapped: dict[str, int] = {item["id"]: 0 for item in criteria}
    for row in parsed_tests:
        if not row["layers"]:
            errors.append(f"Test case {row['test_id']} has no declared layers.")
            continue
        invalid = [layer for layer in row["layers"] if layer not in _ALLOWED_LAYERS]
        if invalid:
            errors.append(
                f"Test case {row['test_id']} has invalid layers: {', '.join(sorted(invalid))}."
            )
        for acceptance_id in row["acceptance_ids"]:
            if acceptance_id in mapped:
                mapped[acceptance_id] += 1

    for acceptance_id, count in mapped.items():
        if count < 1:
            errors.append(f"Acceptance criterion {acceptance_id} is not mapped to any test case.")

    return (len(errors) == 0, sorted(errors))


def _write_compat_latest(contracts_dir: Path, contracts_latest_dir: Path) -> None:
    copy_or_replace_dir(contracts_dir, contracts_latest_dir)


def run(args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    spec_path = (workspace_root / args.spec).resolve()
    skill_run = SkillRun(skill="plan_to_contracts", workspace_root=workspace_root)

    if not spec_path.exists():
        skill_run.add_note(f"SPEC not found: {spec_path}")
        return skill_run.finalize("fail", emit_json=args.json)

    spec_text = spec_path.read_text(encoding="utf-8")
    criteria = parse_acceptance_criteria(spec_text)

    entity_name, route, fields = _infer_core_objects(spec_text)
    if entity_name == "Item":
        skill_run.add_note("Assumption: using generic Item entity because SPEC did not name a domain entity.")

    endpoints = _infer_endpoints(criteria, entity_name)
    generated_test_rows = _generate_test_plan_rows(criteria)

    contracts_dir = workspace_root / "artifacts" / "contracts" / skill_run.timestamp
    contracts_latest_dir = workspace_root / "artifacts" / "contracts" / "latest"
    contracts_dir.mkdir(parents=True, exist_ok=True)

    api_contract_md = contracts_dir / "API_CONTRACT.md"
    data_model_md = contracts_dir / "DATA_MODEL.md"
    routes_md = contracts_dir / "ROUTES.md"
    test_plan_md = contracts_dir / "TEST_PLAN.md"
    api_contract_json = contracts_dir / "api_contract.json"
    contracts_summary_json = contracts_dir / "contracts_summary.json"
    gate_report_md = contracts_dir / "GateReport.md"

    api_contract_md.write_text(_render_api_contract_markdown(endpoints), encoding="utf-8")
    data_model_md.write_text(_render_data_model_markdown(entity_name, fields), encoding="utf-8")
    routes_md.write_text(_render_routes_markdown(route, endpoints), encoding="utf-8")

    if args.test_plan_source:
        source_test_plan = (workspace_root / args.test_plan_source).resolve()
        if not source_test_plan.exists():
            skill_run.add_note(f"Provided test plan source was not found: {source_test_plan}")
            return skill_run.finalize("fail", emit_json=args.json)
        test_plan_text = source_test_plan.read_text(encoding="utf-8")
        skill_run.add_note("Assumption: using caller-provided TEST_PLAN source for validation.")
    else:
        test_plan_text = _render_test_plan_markdown(generated_test_rows)

    test_plan_md.write_text(test_plan_text, encoding="utf-8")

    machine_contract = {
        "schema_version": SCHEMA_VERSION,
        "endpoints": endpoints,
        "entity": {
            "fields": fields,
            "name": entity_name,
        },
    }
    write_json(api_contract_json, machine_contract)

    parsed_tests = parse_test_plan_markdown(test_plan_text)
    mapping_ok, mapping_errors = _validate_mapping(criteria, parsed_tests)

    contracts_summary = {
        "acceptance_criteria": criteria,
        "counts": {
            "acceptance_criteria": len(criteria),
            "endpoints": len(endpoints),
            "entities": 1,
            "routes": 2,
            "test_cases": len(parsed_tests),
        },
        "gate": {
            "errors": mapping_errors,
            "mapping_ok": mapping_ok,
        },
        "spec_path": str(spec_path.relative_to(workspace_root)),
    }
    write_json(contracts_summary_json, contracts_summary)

    gate_lines = ["# Contracts GateReport", ""]
    if mapping_ok:
        gate_lines.extend(
            [
                "Status: PASS",
                "",
                f"Acceptance criteria mapped: {len(criteria)}",
                f"Test cases parsed: {len(parsed_tests)}",
                "",
                "Next fix steps:",
                "1. Run `python -m skills fullstack_test_harness` to validate UI/API/DB against this TEST_PLAN.",
            ]
        )
    else:
        gate_lines.extend(
            [
                "Status: FAIL",
                "",
                "Mapping errors:",
            ]
        )
        for error in mapping_errors:
            gate_lines.append(f"- {error}")
        gate_lines.extend(
            [
                "",
                "Next fix steps:",
                "1. Edit `artifacts/contracts/latest/TEST_PLAN.md` and ensure every AC-### appears in at least one test row.",
                "2. Ensure each test row has layer(s) chosen from `ui`, `api`, `db`.",
                "3. Re-run `python -m skills plan_to_contracts --spec <SPEC.md>`.",
            ]
        )
    gate_report_md.write_text("\n".join(gate_lines) + "\n", encoding="utf-8")

    for path in [
        api_contract_md,
        data_model_md,
        routes_md,
        test_plan_md,
        api_contract_json,
        contracts_summary_json,
        gate_report_md,
    ]:
        skill_run.record_artifact(path)

    _write_compat_latest(contracts_dir, contracts_latest_dir)

    if not mapping_ok:
        skill_run.add_note("Contracts mapping gate failed. See artifacts/contracts/latest/GateReport.md")
        return skill_run.finalize("fail", emit_json=args.json)

    skill_run.add_note("Contracts generated and acceptance-to-test mapping gate passed.")
    return skill_run.finalize("pass", emit_json=args.json)
