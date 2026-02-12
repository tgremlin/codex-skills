from __future__ import annotations

import json
import time
from argparse import Namespace
from pathlib import Path
from typing import Any

from swarm_skills.commands import pipeline
from swarm_skills.runtime import SkillRun, write_json

SCHEMA_VERSION = "1.0"


def _spec_files(spec_dir: Path) -> list[Path]:
    return sorted(path for path in spec_dir.glob("*.md") if path.is_file())


def _template_ids(workspace_root: Path, templates_arg: str) -> list[str]:
    templates_root = workspace_root / "templates"
    if templates_arg.strip().lower() == "all":
        ids = []
        for manifest in sorted(templates_root.glob("*/template.json")):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            template_id = payload.get("id")
            if isinstance(template_id, str) and template_id:
                ids.append(template_id)
        return sorted(set(ids))
    return sorted(set(item.strip() for item in templates_arg.split(",") if item.strip()))


def _rel(path: Path, workspace_root: Path) -> str:
    return str(path.relative_to(workspace_root)) if path.is_relative_to(workspace_root) else str(path)


def run(args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    spec_dir = (workspace_root / args.spec_dir).resolve() if args.spec_dir else (workspace_root / "examples" / "specs")
    templates_arg = str(args.templates) if getattr(args, "templates", None) else "all"
    strict_mode = bool(getattr(args, "strict", False))
    network_mode = bool(getattr(args, "network", False))
    limit = int(getattr(args, "limit", 12) or 0)
    skill_run = SkillRun(skill="matrix", workspace_root=workspace_root, artifact_dir_name="matrix")

    specs = _spec_files(spec_dir)
    template_ids = _template_ids(workspace_root, templates_arg)
    if not specs or not template_ids:
        gate = skill_run.run_dir / "GateReport.md"
        gate.write_text(
            "# Matrix GateReport\n\nStatus: FAIL\n\nMissing specs or templates for matrix generation.\n",
            encoding="utf-8",
        )
        skill_run.record_artifact(gate)
        skill_run.add_note("Matrix requires at least one spec and one template.")
        return skill_run.finalize("fail", emit_json=args.json)

    combos = [(spec, template_id) for spec in specs for template_id in template_ids]
    combos = sorted(combos, key=lambda row: (row[0].name, row[1]))
    if limit > 0:
        combos = combos[:limit]

    rows: list[dict[str, Any]] = []
    total_warnings = 0
    for spec_path, template_id in combos:
        started = time.perf_counter()
        pipeline_args = Namespace(
            workspace_root=str(workspace_root),
            spec=_rel(spec_path, workspace_root),
            template=template_id,
            network=network_mode,
            strict=strict_mode,
            stop_on_fail=True,
            steps=None,
            triage_on_fail=False,
            json=False,
        )
        exit_code = pipeline.run(pipeline_args)
        duration_sec = round(time.perf_counter() - started, 3)

        pipeline_result_path = workspace_root / "artifacts" / "pipeline" / "latest" / "pipeline_result.json"
        payload: dict[str, Any] = {}
        if pipeline_result_path.exists():
            try:
                payload = json.loads(pipeline_result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}

        status = str(payload.get("overall_status", "fail"))
        if exit_code != 0:
            status = "fail"
        warnings = int(payload.get("warnings_count", 0)) if isinstance(payload.get("warnings_count"), int) else 0
        total_warnings += warnings

        rows.append(
            {
                "duration_sec": duration_sec,
                "pipeline_result_json": _rel(pipeline_result_path, workspace_root) if pipeline_result_path.exists() else None,
                "spec_path": _rel(spec_path, workspace_root),
                "status": status,
                "template_id": template_id,
                "warnings_count": warnings,
            }
        )

    rows = sorted(rows, key=lambda row: (row["spec_path"], row["template_id"]))
    any_fail = any(row["status"] == "fail" for row in rows)
    any_warn = any(row["status"] == "warn" for row in rows)
    overall_status = "fail" if any_fail else ("warn" if any_warn else "pass")

    matrix_payload = {
        "combination_count": len(rows),
        "limit": limit,
        "matrix": rows,
        "network_mode": network_mode,
        "overall_status": overall_status,
        "schema_version": SCHEMA_VERSION,
        "spec_dir": _rel(spec_dir, workspace_root),
        "strict_mode": strict_mode,
        "templates": template_ids,
        "warnings_count": total_warnings,
    }
    matrix_path = skill_run.run_dir / "matrix.json"
    write_json(matrix_path, matrix_payload)
    skill_run.record_artifact(matrix_path)

    lines = [
        "# Matrix Report",
        "",
        f"Status: {overall_status.upper()}",
        f"Combinations: {len(rows)}",
        f"Strict mode: `{strict_mode}`",
        f"Network mode: `{network_mode}`",
        "",
        "| spec | template | status | warnings | duration_sec |",
        "|---|---|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['spec_path']}` | `{row['template_id']}` | `{row['status']}` | {row['warnings_count']} | {row['duration_sec']:.3f} |"
        )
    matrix_report_path = skill_run.run_dir / "matrix_report.md"
    matrix_report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    skill_run.record_artifact(matrix_report_path)

    gate_path = skill_run.run_dir / "GateReport.md"
    gate_lines = [
        "# Matrix GateReport",
        "",
        f"Status: {overall_status.upper()}",
        "",
        f"matrix.json: `{_rel(matrix_path, workspace_root)}`",
        f"matrix_report.md: `{_rel(matrix_report_path, workspace_root)}`",
    ]
    if overall_status != "pass":
        gate_lines.extend(
            [
                "",
                "Next fix steps:",
                "1. Inspect failing spec/template rows in matrix_report.md.",
                "2. Open corresponding pipeline_result.json paths and resolve root causes.",
                "3. Re-run `python -m skills matrix`.",
            ]
        )
    gate_path.write_text("\n".join(gate_lines) + "\n", encoding="utf-8")
    skill_run.record_artifact(gate_path)

    if overall_status == "fail":
        skill_run.add_note("Matrix contains failing combinations.")
    elif overall_status == "warn":
        skill_run.add_note("Matrix completed with warnings.")
    else:
        skill_run.add_note("Matrix completed successfully.")

    return skill_run.finalize(
        "fail" if overall_status == "fail" else "pass",
        emit_json=args.json,
        summary_updates={
            "overall_status": overall_status,
            "schema_version": SCHEMA_VERSION,
            "warnings_count": total_warnings,
        },
    )
