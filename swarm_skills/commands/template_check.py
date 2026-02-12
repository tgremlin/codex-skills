from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swarm_skills.runtime import SkillRun, write_json

SCHEMA_VERSION = "1.0"


def _resolve_manifests(workspace_root: Path, template_ref: str | None, all_templates: bool) -> list[Path]:
    templates_root = workspace_root / "templates"
    if all_templates:
        return sorted(templates_root.glob("*/template.json"))

    if not template_ref:
        return []

    by_id = templates_root / template_ref / "template.json"
    if by_id.exists():
        return [by_id]

    ref_path = (workspace_root / template_ref).resolve()
    if ref_path.is_file() and ref_path.name == "template.json":
        return [ref_path]
    manifest = ref_path / "template.json"
    if manifest.exists():
        return [manifest]
    return []


def _has_test_cmd(health_strategy: Any) -> bool:
    if not isinstance(health_strategy, list):
        return False
    for item in health_strategy:
        if isinstance(item, str) and item.startswith("test_cmd:") and item.split(":", 1)[1].strip():
            return True
    return False


def _validate_manifest(manifest_path: Path, strict_mode: bool, workspace_root: Path) -> dict[str, Any]:
    rel_path = (
        str(manifest_path.relative_to(workspace_root))
        if manifest_path.is_relative_to(workspace_root)
        else str(manifest_path)
    )
    result = {
        "checks": {},
        "errors": [],
        "manifest_path": rel_path,
        "status": "pass",
        "template_id": None,
        "warnings": [],
    }

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result["errors"].append(f"Invalid JSON: {exc}")
        result["status"] = "fail"
        return result

    template_id = payload.get("id")
    result["template_id"] = str(template_id) if template_id else manifest_path.parent.name

    required_fields = ["id", "name", "version"]
    for field in required_fields:
        ok = field in payload and isinstance(payload.get(field), str) and bool(str(payload.get(field)).strip())
        result["checks"][field] = ok
        if not ok:
            result["errors"].append(f"Missing required field `{field}`.")

    capabilities = payload.get("capabilities")
    capabilities_ok = isinstance(capabilities, list) and all(
        isinstance(item, str) and item.strip() for item in capabilities
    )
    result["checks"]["capabilities_array"] = capabilities_ok
    if not capabilities_ok:
        result["errors"].append("Missing required field `capabilities` as non-empty string array.")

    boot = payload.get("boot")
    health_strategy = boot.get("health_strategy") if isinstance(boot, dict) else None
    health_has_test_cmd = _has_test_cmd(health_strategy)
    result["checks"]["health_strategy_test_cmd"] = health_has_test_cmd
    if not health_has_test_cmd:
        result["errors"].append(
            "Missing required `boot.health_strategy` test_cmd for no-network harness compatibility."
        )

    inventory_cmd = boot.get("inventory_cmd") if isinstance(boot, dict) else None
    inventory_ok = isinstance(inventory_cmd, list) and len(inventory_cmd) > 0
    result["checks"]["inventory_cmd_recommended"] = inventory_ok
    if not inventory_ok:
        result["warnings"].append("Recommended field `boot.inventory_cmd` is missing.")

    if result["errors"]:
        result["status"] = "fail"
    elif result["warnings"] and strict_mode:
        result["errors"].append("Strict mode escalated recommended-field warnings to failure.")
        result["status"] = "fail"
    elif result["warnings"]:
        result["status"] = "warn"
    else:
        result["status"] = "pass"

    return result


def run(args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    strict_mode = bool(getattr(args, "strict", False))
    skill_run = SkillRun(skill="template_check", workspace_root=workspace_root, artifact_dir_name="template_check")

    manifests = _resolve_manifests(
        workspace_root=workspace_root,
        template_ref=getattr(args, "template", None),
        all_templates=bool(getattr(args, "all", False)),
    )
    if not manifests:
        gate_report = skill_run.run_dir / "GateReport.md"
        gate_report.write_text(
            "# Template Check GateReport\n\nStatus: FAIL\n\nNo templates matched. Use `--template <id>` or `--all`.\n",
            encoding="utf-8",
        )
        skill_run.record_artifact(gate_report)
        skill_run.add_note("No template manifests were selected.")
        return skill_run.finalize("fail", emit_json=args.json)

    results = [
        _validate_manifest(manifest_path=manifest, strict_mode=strict_mode, workspace_root=workspace_root)
        for manifest in manifests
    ]

    failed = [row for row in results if row["status"] == "fail"]
    warned = [row for row in results if row["status"] == "warn"]
    passed = [row for row in results if row["status"] == "pass"]

    if failed:
        overall_status = "fail"
    elif warned:
        overall_status = "warn"
    else:
        overall_status = "pass"

    report_payload = {
        "overall_status": overall_status,
        "schema_version": SCHEMA_VERSION,
        "strict_mode": strict_mode,
        "templates": sorted(results, key=lambda row: str(row["template_id"])),
        "totals": {
            "failed": len(failed),
            "passed": len(passed),
            "templates": len(results),
            "warned": len(warned),
        },
    }

    report_path = skill_run.run_dir / "report.json"
    write_json(report_path, report_payload)
    skill_run.record_artifact(report_path)

    gate_lines = [
        "# Template Check GateReport",
        "",
        f"Status: {overall_status.upper()}",
        f"Strict mode: `{strict_mode}`",
        "",
        f"Templates checked: {len(results)}",
        f"Failed: {len(failed)}",
        f"Warnings: {len(warned)}",
        "",
    ]
    for row in sorted(results, key=lambda item: str(item["template_id"])):
        gate_lines.append(f"## {row['template_id']}")
        gate_lines.append(f"- status: `{row['status']}`")
        gate_lines.append(f"- manifest: `{row['manifest_path']}`")
        if row["errors"]:
            gate_lines.append("- errors:")
            for item in row["errors"]:
                gate_lines.append(f"  - {item}")
        if row["warnings"]:
            gate_lines.append("- warnings:")
            for item in row["warnings"]:
                gate_lines.append(f"  - {item}")
        gate_lines.append("")

    gate_lines.extend(
        [
            "Next fix steps:",
            "1. Add missing required metadata and no-network `test_cmd` health strategy to failing templates.",
            "2. Add `boot.inventory_cmd` to remove warnings and improve deterministic backend inventory.",
            "3. Re-run `python -m skills template_check --all`.",
        ]
    )

    gate_path = skill_run.run_dir / "GateReport.md"
    gate_path.write_text("\n".join(gate_lines) + "\n", encoding="utf-8")
    skill_run.record_artifact(gate_path)

    if overall_status == "fail":
        skill_run.add_note("Template compliance check failed.")
    elif overall_status == "warn":
        skill_run.add_note("Template compliance check passed with warnings.")
    else:
        skill_run.add_note("Template compliance check passed.")

    return skill_run.finalize(
        "fail" if overall_status == "fail" else "pass",
        emit_json=args.json,
        summary_updates={
            "overall_status": overall_status,
            "strict_mode": strict_mode,
            "warnings_count": len(warned),
        },
    )
