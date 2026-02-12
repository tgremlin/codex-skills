from __future__ import annotations

import json
import time
from argparse import Namespace
from pathlib import Path
from typing import Any

from swarm_skills.commands import pipeline
from swarm_skills.runtime import SkillRun, run_command, utc_now_iso, write_json

SCHEMA_VERSION = "1.0"


def _spec_files(spec_dir: Path) -> list[Path]:
    return sorted(path for path in spec_dir.glob("*.md") if path.is_file())


def _relative_or_abs(path: Path, workspace_root: Path) -> str:
    return str(path.relative_to(workspace_root)) if path.is_relative_to(workspace_root) else str(path)


def _resolve_repo_commit(workspace_root: Path) -> str:
    result = run_command(["git", "rev-parse", "HEAD"], cwd=workspace_root, timeout_sec=10)
    if result.exit_code == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "unknown"


def _append_history(history_path: Path, entry: dict[str, Any]) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def run(args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    spec_dir = (workspace_root / args.spec_dir).resolve() if args.spec_dir else (workspace_root / "examples" / "specs")
    strict_mode = bool(getattr(args, "strict", False))
    network_mode = bool(getattr(args, "network", False))
    forced_template = getattr(args, "template", None)
    append_history = bool(getattr(args, "append_history", False))
    skill_run = SkillRun(skill="bench", workspace_root=workspace_root, artifact_dir_name="bench")
    started_at = skill_run.started_at

    specs = _spec_files(spec_dir)
    if not specs:
        gate = skill_run.run_dir / "bench_report.md"
        gate.write_text(
            "# Bench Report\n\nStatus: FAIL\n\nNo spec files found. Add `*.md` files under the spec directory.\n",
            encoding="utf-8",
        )
        skill_run.record_artifact(gate)
        skill_run.add_note(f"No specs found in {spec_dir}")
        return skill_run.finalize("fail", emit_json=args.json)

    results: list[dict[str, Any]] = []
    total_warnings = 0

    for spec_path in specs:
        started = time.perf_counter()
        pipeline_args = Namespace(
            workspace_root=str(workspace_root),
            spec=_relative_or_abs(spec_path, workspace_root),
            template=forced_template,
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

        warnings_count = int(payload.get("warnings_count", 0)) if isinstance(payload.get("warnings_count", 0), int) else 0
        total_warnings += warnings_count
        step_durations = []
        for row in payload.get("steps", []):
            if isinstance(row, dict):
                step_durations.append(
                    {
                        "duration_sec": float(row.get("duration_sec", 0.0)),
                        "status": str(row.get("status", "unknown")),
                        "step_name": str(row.get("step_name", "unknown")),
                    }
                )

        results.append(
            {
                "duration_sec": duration_sec,
                "pipeline_result_json": _relative_or_abs(pipeline_result_path, workspace_root) if pipeline_result_path.exists() else None,
                "spec_path": _relative_or_abs(spec_path, workspace_root),
                "status": status,
                "step_durations": step_durations,
                "template": payload.get("template", {"template_id": forced_template, "template_version": None}),
                "warnings_count": warnings_count,
            }
        )

    results = sorted(results, key=lambda row: row["spec_path"])
    any_fail = any(row["status"] == "fail" for row in results)
    any_warn = any(row["status"] == "warn" for row in results)
    overall_status = "fail" if any_fail else ("warn" if any_warn else "pass")
    pass_count = sum(1 for row in results if row["status"] == "pass")
    fail_count = sum(1 for row in results if row["status"] == "fail")
    warn_count = sum(1 for row in results if row["status"] == "warn")
    ended_at = utc_now_iso()

    bench_results = {
        "network_mode": network_mode,
        "overall_status": overall_status,
        "results": results,
        "schema_version": SCHEMA_VERSION,
        "spec_dir": _relative_or_abs(spec_dir, workspace_root),
        "strict_mode": strict_mode,
        "warnings_count": total_warnings,
    }
    bench_results_path = skill_run.run_dir / "bench_results.json"
    write_json(bench_results_path, bench_results)
    skill_run.record_artifact(bench_results_path)

    history_path: Path | None = None
    if append_history:
        history_path = workspace_root / "artifacts" / "bench" / "history.jsonl"
        history_entry = {
            "ended_at": ended_at,
            "overall_counts": {
                "fail": fail_count,
                "pass": pass_count,
                "warn": warn_count,
            },
            "per_spec": [
                {
                    "duration_sec": row["duration_sec"],
                    "spec_path": row["spec_path"],
                    "status": row["status"],
                    "warnings_count": row["warnings_count"],
                }
                for row in results
            ],
            "repo_commit": _resolve_repo_commit(workspace_root),
            "spec_dir": _relative_or_abs(spec_dir, workspace_root),
            "started_at": started_at,
            "strict_mode": strict_mode,
            "warnings_count": total_warnings,
        }
        _append_history(history_path, history_entry)
        skill_run.record_artifact(history_path)
        skill_run.add_note("Bench history entry appended.")

    lines = [
        "# Bench Report",
        "",
        f"Status: {overall_status.upper()}",
        f"Spec dir: `{bench_results['spec_dir']}`",
        f"Strict mode: `{strict_mode}`",
        f"Network mode: `{network_mode}`",
        "",
        "| spec | status | warnings | duration_sec |",
        "|---|---:|---:|---:|",
    ]
    for row in results:
        lines.append(
            f"| `{row['spec_path']}` | `{row['status']}` | {row['warnings_count']} | {row['duration_sec']:.3f} |"
        )
    lines.extend(
        [
            "",
            "Highlights:",
            f"- Total specs: {len(results)}",
            f"- Total warnings: {total_warnings}",
            "",
            "Next fix steps:",
            "1. Open failed pipeline_result.json pointers from `bench_results.json`.",
            "2. Apply minimal fixes and re-run `python -m skills bench`.",
        ]
    )
    if history_path is not None:
        lines.extend(["", f"History log: `{_relative_or_abs(history_path, workspace_root)}`"])
    bench_report_path = skill_run.run_dir / "bench_report.md"
    bench_report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    skill_run.record_artifact(bench_report_path)

    if overall_status == "fail":
        skill_run.add_note("Bench failed for at least one spec.")
    elif overall_status == "warn":
        skill_run.add_note("Bench completed with warnings.")
    else:
        skill_run.add_note("Bench completed successfully.")

    return skill_run.finalize(
        "fail" if overall_status == "fail" else "pass",
        emit_json=args.json,
        summary_updates={
            "overall_status": overall_status,
            "spec_count": len(results),
            "strict_mode": strict_mode,
            "warnings_count": total_warnings,
        },
    )
