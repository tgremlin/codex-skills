from __future__ import annotations

import hashlib
import json
import platform
import time
from argparse import Namespace
from pathlib import Path
from typing import Any, Callable

from swarm_skills.catalog import resolve_template
from swarm_skills.commands import (
    backend_build,
    doctor,
    frontend_bind,
    fullstack_test_harness,
    plan_to_contracts,
    scaffold_verify,
    template_select,
    triage_and_patch,
)
from swarm_skills.runtime import SkillRun, run_command, utc_now_iso, write_json

SCHEMA_VERSION = "1.0"
HANDOFF_CONTRACT_REL = "skills/handoff_contract.json"


def _default_steps() -> list[str]:
    return [
        "template_select",
        "scaffold_verify",
        "plan_to_contracts",
        "backend_build",
        "frontend_bind",
        "fullstack_test_harness",
    ]


def _parse_steps(raw: str | None) -> list[str]:
    if not raw:
        return _default_steps()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _read_selected_template(workspace_root: Path) -> str | None:
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


def _summary_pointer(workspace_root: Path, step: str) -> Path:
    mapping = {
        "backend_build": workspace_root / "artifacts" / "backend" / "latest" / "summary.json",
        "doctor": workspace_root / "artifacts" / "doctor" / "latest" / "summary.json",
        "frontend_bind": workspace_root / "artifacts" / "frontend" / "latest" / "summary.json",
        "fullstack_test_harness": workspace_root / "artifacts" / "tests" / "latest" / "summary.json",
        "plan_to_contracts": workspace_root / "artifacts" / "contracts" / "latest" / "summary.json",
        "scaffold_verify": workspace_root / "artifacts" / "scaffold_verify" / "latest" / "summary.json",
        "template_select": workspace_root / "artifacts" / "template_select" / "latest" / "summary.json",
    }
    return mapping.get(step, workspace_root / "artifacts" / step / "latest" / "summary.json")


def _gate_pointer(workspace_root: Path, step: str) -> Path | None:
    mapping = {
        "backend_build": workspace_root / "artifacts" / "backend" / "latest" / "GateReport.md",
        "frontend_bind": workspace_root / "artifacts" / "frontend" / "latest" / "GateReport.md",
        "fullstack_test_harness": workspace_root / "artifacts" / "tests" / "latest" / "GateReport.md",
        "plan_to_contracts": workspace_root / "artifacts" / "contracts" / "latest" / "GateReport.md",
    }
    return mapping.get(step)


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_provenance(workspace_root: Path) -> dict[str, str | None]:
    commit = "unknown"
    commit_result = run_command(["git", "rev-parse", "HEAD"], cwd=workspace_root, timeout_sec=10)
    if commit_result.exit_code == 0 and commit_result.stdout.strip():
        commit = commit_result.stdout.strip()

    node_version: str | None = None
    node_result = run_command(["node", "--version"], cwd=workspace_root, timeout_sec=10)
    if node_result.exit_code == 0:
        node_version = node_result.stdout.strip()

    return {
        "node_version": node_version,
        "python_version": platform.python_version(),
        "repo_commit": commit,
    }


def _artifact_run_dir_from_summary(summary_payload: dict[str, Any]) -> str | None:
    artifacts = summary_payload.get("artifacts", [])
    if not isinstance(artifacts, list):
        return None
    for artifact in sorted(map(str, artifacts)):
        parts = Path(artifact).parts
        if len(parts) < 4:
            continue
        if parts[0] != "artifacts":
            continue
        timestamp = parts[2]
        if timestamp == "latest":
            continue
        return "/".join(parts[:3])
    return None


def _template_info(workspace_root: Path, template_id: str | None) -> dict[str, str | None]:
    if not template_id:
        return {"template_id": None, "template_version": None}
    try:
        resolved = resolve_template(template_id, workspace_root)
        return {"template_id": resolved.id, "template_version": resolved.version}
    except FileNotFoundError:
        return {"template_id": template_id, "template_version": None}


def run(args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    skill_run = SkillRun(skill="pipeline", workspace_root=workspace_root, artifact_dir_name="pipeline")

    steps = _parse_steps(args.steps)
    stop_on_fail = bool(args.stop_on_fail)
    triage_on_fail = bool(getattr(args, "triage_on_fail", False))

    handlers: dict[str, Callable[[Any], int]] = {
        "doctor": doctor.run,
        "template_select": template_select.run,
        "scaffold_verify": scaffold_verify.run,
        "plan_to_contracts": plan_to_contracts.run,
        "backend_build": backend_build.run,
        "frontend_bind": frontend_bind.run,
        "fullstack_test_harness": fullstack_test_harness.run,
    }

    results: list[dict[str, Any]] = []
    warnings_count = 0
    selected_template = args.template
    strict_mode = bool(getattr(args, "strict", False))

    for index, step in enumerate(steps):
        handler = handlers.get(step)
        if handler is None:
            results.append(
                {
                    "artifacts_dir": None,
                    "duration_sec": 0.0,
                    "gate_report_path": None,
                    "notes": ["Unknown step id; verify --steps values."],
                    "status": "fail",
                    "step_name": step,
                    "summary_json_path": None,
                }
            )
            if stop_on_fail:
                for skipped in steps[index + 1 :]:
                    results.append(
                        {
                            "artifacts_dir": None,
                            "duration_sec": 0.0,
                            "gate_report_path": None,
                            "notes": ["Skipped due to prior failure and --stop-on-fail."],
                            "status": "skipped",
                            "step_name": skipped,
                            "summary_json_path": None,
                        }
                    )
                break
            continue

        step_args = Namespace(workspace_root=str(workspace_root), json=False)
        step_started = time.perf_counter()

        if step == "doctor":
            pass
        elif step == "template_select":
            step_args.spec = args.spec
            step_args.auth = None
            step_args.crud = None
            step_args.realtime = None
            step_args.seo = None
        elif step == "scaffold_verify":
            step_args.template = selected_template or _read_selected_template(workspace_root) or "local-node-http-crud"
            step_args.port = "auto"
            step_args.health_timeout_sec = 15
        elif step == "plan_to_contracts":
            step_args.spec = args.spec
            step_args.test_plan_source = None
        elif step == "backend_build":
            step_args.contracts = "artifacts/contracts/latest/api_contract.json"
            step_args.template = selected_template or _read_selected_template(workspace_root) or "local-node-http-crud"
            step_args.backend_root = None
        elif step == "frontend_bind":
            step_args.contracts_dir = "artifacts/contracts/latest"
            step_args.template = selected_template or _read_selected_template(workspace_root) or "local-node-http-crud"
            step_args.frontend_root = None
            step_args.allowlist_config = None
            step_args.strict = strict_mode
        elif step == "fullstack_test_harness":
            step_args.test_plan = "artifacts/contracts/latest/TEST_PLAN.md"
            step_args.template = selected_template or _read_selected_template(workspace_root) or "local-node-http-crud"
            step_args.network = bool(args.network)
            step_args.health_timeout_sec = 15

        exit_code = handler(step_args)
        duration_sec = round(time.perf_counter() - step_started, 3)

        if step == "template_select":
            selected_template = _read_selected_template(workspace_root) or selected_template

        summary_path = _summary_pointer(workspace_root, step)
        gate_path = _gate_pointer(workspace_root, step)
        summary_rel = str(summary_path.relative_to(workspace_root)) if summary_path.exists() else None
        gate_rel = str(gate_path.relative_to(workspace_root)) if gate_path and gate_path.exists() else None
        notes: list[str] = []
        artifacts_dir: str | None = None
        warnings_detected = False

        if summary_path.exists():
            try:
                summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
                summary_notes = summary_payload.get("notes", [])
                if isinstance(summary_notes, list):
                    notes.extend([str(item) for item in summary_notes[:4]])
                artifacts_dir = _artifact_run_dir_from_summary(summary_payload)
            except json.JSONDecodeError:
                notes.append("Summary JSON could not be parsed.")

        if gate_path and gate_path.exists():
            gate_text = gate_path.read_text(encoding="utf-8", errors="ignore")
            if "\nWarnings:" in gate_text or gate_text.startswith("Warnings:"):
                warnings_detected = True
                notes.append("GateReport contains warnings.")

        if exit_code != 0:
            status = "fail"
        elif warnings_detected:
            status = "warn"
            warnings_count += 1
        else:
            status = "pass"

        results.append(
            {
                "artifacts_dir": artifacts_dir,
                "duration_sec": duration_sec,
                "gate_report_path": gate_rel,
                "notes": sorted(set(notes)),
                "status": status,
                "step_name": step,
                "summary_json_path": summary_rel,
            }
        )

        if exit_code != 0 and stop_on_fail:
            for skipped in steps[index + 1 :]:
                results.append(
                    {
                        "artifacts_dir": None,
                        "duration_sec": 0.0,
                        "gate_report_path": None,
                        "notes": ["Skipped due to prior failure and --stop-on-fail."],
                        "status": "skipped",
                        "step_name": skipped,
                        "summary_json_path": None,
                    }
                )
            break

    any_failed = any(item["status"] == "fail" for item in results)
    if any_failed:
        overall_status = "fail"
    elif warnings_count > 0:
        overall_status = "warn"
    else:
        overall_status = "pass"

    gate_path = skill_run.run_dir / "GateReport.md"

    lines = ["# Pipeline GateReport", "", f"Status: {overall_status.upper()}", "", "Step results:"]
    for row in results:
        lines.append(f"- `{row['step_name']}`: {row['status']}")
        if row["summary_json_path"]:
            lines.append(f"  - summary: `{row['summary_json_path']}`")
        if row["gate_report_path"]:
            lines.append(f"  - gate: `{row['gate_report_path']}`")

    failing = next((row for row in results if row["status"] == "fail"), None)
    if failing:
        lines.extend(
            [
                "",
                f"Failed step: `{failing['step_name']}`",
                "",
                "Next fix steps:",
                "1. Open the failing step summary and gate report paths listed above.",
                "2. Apply minimal fix in the relevant code/template.",
                "3. Re-run `python -m skills pipeline --spec <SPEC.md>`.",
            ]
        )

    gate_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    skill_run.record_artifact(gate_path)

    triage_payload: dict[str, Any] | None = None
    if failing and triage_on_fail:
        failing_gate_reports: list[str] = []
        for row in results:
            if row["status"] != "fail":
                continue
            gate_rel = row.get("gate_report_path")
            if not gate_rel:
                continue
            gate_abs = (workspace_root / str(gate_rel)).resolve()
            if gate_abs.exists():
                failing_gate_reports.append(str(gate_abs))
        if not failing_gate_reports:
            failing_gate_reports.append(str(gate_path.resolve()))

        triage_args = Namespace(
            workspace_root=str(workspace_root),
            gate_report=failing_gate_reports,
            logs=None,
            artifacts_root="artifacts",
            contracts="artifacts/contracts/latest",
            json=False,
        )
        triage_exit = triage_and_patch.run(triage_args)
        triage_latest = workspace_root / "artifacts" / "triage" / "latest"
        triage_summary_path = triage_latest / "summary.json"
        triage_artifacts_dir: str | None = None
        if triage_summary_path.exists():
            try:
                triage_summary_payload = json.loads(triage_summary_path.read_text(encoding="utf-8"))
                triage_artifacts_dir = _artifact_run_dir_from_summary(triage_summary_payload)
            except json.JSONDecodeError:
                triage_artifacts_dir = None

        triage_payload = {
            "artifacts_dir": triage_artifacts_dir,
            "patch_plan_path": "artifacts/triage/latest/patch_plan.md" if (triage_latest / "patch_plan.md").exists() else None,
            "root_cause_path": "artifacts/triage/latest/root_cause.md" if (triage_latest / "root_cause.md").exists() else None,
            "status": "pass" if triage_exit == 0 else "fail",
        }
        lines.extend(
            [
                "",
                "Triage generated:",
                f"- status: `{triage_payload['status']}`",
                f"- root_cause: `{triage_payload['root_cause_path']}`",
                f"- patch_plan: `{triage_payload['patch_plan_path']}`",
            ]
        )
        gate_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    spec_path = (workspace_root / args.spec).resolve() if args.spec else None
    ended_at = utc_now_iso()
    template_info = _template_info(workspace_root, selected_template or _read_selected_template(workspace_root))
    handoff_contract_path = workspace_root / HANDOFF_CONTRACT_REL
    pipeline_result = {
        "ended_at": ended_at,
        "handoff_contract_path": HANDOFF_CONTRACT_REL if handoff_contract_path.exists() else None,
        "handoff_contract_sha256": _sha256_file(handoff_contract_path) if handoff_contract_path.exists() else None,
        "overall_status": overall_status,
        "provenance": _resolve_provenance(workspace_root),
        "schema_version": SCHEMA_VERSION,
        "spec": {
            "spec_path": str(spec_path.relative_to(workspace_root)) if spec_path and spec_path.exists() and spec_path.is_relative_to(workspace_root) else (str(spec_path) if spec_path else None),
            "spec_sha256": _sha256_file(spec_path) if spec_path else None,
        },
        "started_at": skill_run.started_at,
        "steps": results,
        "strict_mode": strict_mode,
        "template": template_info,
        "triage": triage_payload,
        "warnings_count": warnings_count,
    }

    pipeline_result_path = skill_run.run_dir / "pipeline_result.json"
    write_json(pipeline_result_path, pipeline_result)
    skill_run.record_artifact(pipeline_result_path)

    summary_payload = {
        "overall_status": overall_status,
        "pipeline_result_json": str(pipeline_result_path.relative_to(workspace_root)),
        "status": "fail" if overall_status == "fail" else "pass",
        "strict_mode": strict_mode,
        "steps": results,
        "stop_on_fail": stop_on_fail,
        "steps_requested": steps,
        "triage": triage_payload,
        "warnings_count": warnings_count,
    }

    if failing:
        skill_run.add_note(f"Pipeline failed at step: {failing['step_name']}")
        if triage_payload is not None:
            skill_run.add_note(f"Auto-triage status: {triage_payload['status']}")
    elif overall_status == "warn":
        skill_run.add_note("Pipeline completed with warnings.")
    else:
        skill_run.add_note("Pipeline completed successfully.")

    return skill_run.finalize(
        "fail" if overall_status == "fail" else "pass",
        emit_json=args.json,
        summary_updates=summary_payload,
    )
