from __future__ import annotations

import asyncio
import json
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from swarm_skills.runtime import copy_or_replace_dir, utc_now_iso, utc_timestamp, write_json
from swarm_skills.swarm import executor, integrator, routing, selection
from swarm_skills.swarm.models import ExpertAssignment, ExpertResult, SwarmArtifacts, required_experts
from swarm_skills.swarm.policy import EXPERT_DEFINITIONS, GLOBAL_SAFETY_RULES, required_output_schema
from swarm_skills.swarm.spec_resolution import resolve_spec, write_resolution_record


NO_SPEC_MESSAGE = (
    "No spec found.\n"
    "Options:\n"
    "  1) codex-swarm gen-spec --repo . --goal '<goal>'\n"
    "  2) codex-swarm run --repo . --goal '<goal>' --gen-spec-if-missing\n"
    "  3) Provide --spec /path/to/spec.md"
)


def _to_rel(path: Path, root: Path) -> str:
    if path.resolve().is_relative_to(root.resolve()):
        return path.resolve().relative_to(root.resolve()).as_posix()
    return path.resolve().as_posix()


def create_swarm_artifacts(repo_root: Path) -> SwarmArtifacts:
    ts = utc_timestamp()
    run_dir = repo_root / "artifacts" / "swarm_run" / ts
    latest_dir = repo_root / "artifacts" / "swarm_run" / "latest"
    patches_dir = run_dir / "patches"
    transcripts_dir = run_dir / "transcripts"
    gate_reports_dir = run_dir / "gate_reports"

    for path in [run_dir, patches_dir, transcripts_dir, gate_reports_dir]:
        path.mkdir(parents=True, exist_ok=True)

    return SwarmArtifacts(
        repo_root=repo_root,
        run_dir=run_dir,
        latest_dir=latest_dir,
        patches_dir=patches_dir,
        transcripts_dir=transcripts_dir,
        gate_reports_dir=gate_reports_dir,
    )


def _build_assignments(experts: list[str], goal: str, spec_path: str) -> list[ExpertAssignment]:
    schema = required_output_schema()
    assignments: list[ExpertAssignment] = []
    for expert in experts:
        definition = EXPERT_DEFINITIONS[expert]
        assignments.append(
            ExpertAssignment(
                expert=expert,
                role_prompt=definition.role_prompt,
                task=(
                    f"Goal: {goal}\n"
                    f"Use SPEC at: {spec_path}\n"
                    "Make minimal deterministic code edits and keep changes scoped to allowed paths."
                ),
                allowed_paths=list(definition.allowed_paths),
                required_output_schema=schema,
            )
        )
    return assignments


def _summarize_assignment_rows(assignments: list[ExpertAssignment]) -> list[dict[str, Any]]:
    rows = []
    for item in assignments:
        rows.append(
            {
                "expert": item.expert,
                "allowed_paths": item.allowed_paths,
                "task": item.task,
                "required_output_schema": item.required_output_schema,
                "prompt_path": item.prompt_path,
            }
        )
    return rows


def _run_sync(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _prepare_integration_worktree(repo_root: Path, run_dir: Path) -> tuple[Path | None, str | None, str | None]:
    integration_dir = run_dir / "integration"
    branch_name = f"codex-swarm/{run_dir.name}"

    if integration_dir.exists():
        _run_sync(["git", "worktree", "remove", "--force", str(integration_dir)], cwd=repo_root)

    result = _run_sync(["git", "worktree", "add", "-B", branch_name, str(integration_dir), "HEAD"], cwd=repo_root)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git worktree add failed").strip()
        return None, None, detail

    base_ref = _run_sync(["git", "rev-parse", "HEAD"], cwd=integration_dir)
    if base_ref.returncode != 0:
        return None, None, "git rev-parse failed in integration worktree"

    return integration_dir, base_ref.stdout.strip(), None


def _refresh_base_ref(integration_dir: Path) -> str:
    result = _run_sync(["git", "rev-parse", "HEAD"], cwd=integration_dir)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "HEAD"


def _apply_patch_into_integration(integration_dir: Path):
    return integrator.build_git_apply(integration_dir)


def _commit_integration_if_dirty(integration_dir: Path, iteration: int) -> str | None:
    status = _run_sync(["git", "status", "--porcelain"], cwd=integration_dir)
    if status.returncode != 0 or not status.stdout.strip():
        return None

    _run_sync(["git", "add", "-A"], cwd=integration_dir)
    commit = _run_sync(["git", "commit", "-m", f"codex-swarm iteration {iteration}"], cwd=integration_dir)
    if commit.returncode != 0:
        return None

    rev = _run_sync(["git", "rev-parse", "HEAD"], cwd=integration_dir)
    if rev.returncode == 0 and rev.stdout.strip():
        return rev.stdout.strip()
    return None


def _run_pipeline_gate(integration_dir: Path, spec_path: Path) -> tuple[str, dict[str, Any] | None, str]:
    cmd = [
        "python3",
        "-m",
        "skills",
        "pipeline",
        "--workspace-root",
        str(integration_dir),
        "--spec",
        spec_path.resolve().as_posix(),
        "--orchestrator",
    ]
    completed = _run_sync(cmd, cwd=integration_dir)

    payload: dict[str, Any] | None = None
    try:
        payload = json.loads((completed.stdout or "").strip() or "{}")
        if not isinstance(payload, dict):
            payload = None
    except json.JSONDecodeError:
        payload = None

    gate_report_path = integration_dir / "artifacts" / "pipeline" / "latest" / "GateReport.md"
    gate_text = gate_report_path.read_text(encoding="utf-8", errors="ignore") if gate_report_path.exists() else ""

    if payload and payload.get("overall_status") == "pass":
        return "pass", payload, gate_text

    if completed.returncode == 0 and payload:
        status = str(payload.get("overall_status") or payload.get("status") or "fail")
        return ("pass" if status == "pass" else "fail"), payload, gate_text

    return "fail", payload, gate_text


def _simulate_gate(iteration: int) -> tuple[str, dict[str, Any], str]:
    payload = {
        "schema_version": "1.0",
        "overall_status": "pass",
        "steps": [
            {
                "step_name": "simulated",
                "status": "pass",
            }
        ],
        "iteration": iteration,
    }
    gate_text = "# Simulated GateReport\n\nStatus: PASS\n"
    return "pass", payload, gate_text


def _resolve_selection(
    *,
    repo_root: Path,
    goal: str,
    max_experts: int,
    planner_augmentation: bool,
    codex_bin: str,
    timeout_sec: int,
) -> tuple[list[str], str | None]:
    selected = selection.select_experts_deterministic(repo_root, goal, max_experts)
    if not planner_augmentation:
        return selected, None

    augmented, planner_note = selection.planner_augment_experts(
        repo_root=repo_root,
        goal=goal,
        current=selected,
        codex_bin=codex_bin,
        timeout_sec=timeout_sec,
    )

    final: list[str] = []
    for expert in list(required_experts()) + augmented:
        if expert not in final:
            final.append(expert)

    return final[: max(max_experts, len(required_experts()))], planner_note


def _lead_conflict_experts(conflicts: list[dict[str, Any]], max_experts: int) -> list[str]:
    experts = list(required_experts())
    for row in conflicts:
        name = str(row.get("expert") or "")
        if name and name not in experts:
            experts.append(name)
    return experts[: max(max_experts, len(required_experts()))]


def run_plan(args: Any) -> int:
    repo_root = Path(args.repo).resolve()
    artifacts = create_swarm_artifacts(repo_root)

    spec_record, spec_path = resolve_spec(
        repo_root=repo_root,
        provided_spec=args.spec,
        goal=args.goal,
        gen_if_missing=bool(args.gen_spec_if_missing),
    )
    write_resolution_record(artifacts.run_dir / "spec_resolution.json", spec_record)

    if spec_path is None:
        print(NO_SPEC_MESSAGE)
        copy_or_replace_dir(artifacts.run_dir, artifacts.latest_dir)
        return 2

    experts, planner_note = _resolve_selection(
        repo_root=repo_root,
        goal=args.goal,
        max_experts=args.max_experts,
        planner_augmentation=bool(args.planner_augmentation),
        codex_bin=args.codex_bin,
        timeout_sec=args.codex_timeout_sec,
    )
    assignments = _build_assignments(experts, args.goal, _to_rel(spec_path, repo_root))

    plan_payload = {
        "schema_version": "1.0",
        "mode": "plan",
        "goal": args.goal,
        "spec": _to_rel(spec_path, repo_root),
        "experts": experts,
        "safety_rules": GLOBAL_SAFETY_RULES,
        "budgets": {
            "max_iterations": args.max_iterations,
            "max_experts": args.max_experts,
            "time_budget_sec": args.time_budget,
            "max_diff_lines": args.max_diff_lines,
        },
        "planner_note": planner_note,
        "created_at": utc_now_iso(),
    }
    write_json(artifacts.run_dir / "plan.json", plan_payload)
    write_json(artifacts.run_dir / "assignments.json", {"assignments": _summarize_assignment_rows(assignments)})

    summary = {
        "status": "pass",
        "mode": "plan",
        "run_dir": _to_rel(artifacts.run_dir, repo_root),
        "plan": _to_rel(artifacts.run_dir / "plan.json", repo_root),
        "assignments": _to_rel(artifacts.run_dir / "assignments.json", repo_root),
    }
    write_json(artifacts.run_dir / "summary.json", summary)
    copy_or_replace_dir(artifacts.run_dir, artifacts.latest_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _write_gate_iteration(artifacts: SwarmArtifacts, iteration: int, payload: dict[str, Any], gate_text: str) -> None:
    write_json(artifacts.gate_reports_dir / f"iteration_{iteration:02d}.json", payload)
    (artifacts.gate_reports_dir / f"iteration_{iteration:02d}.GateReport.md").write_text(gate_text, encoding="utf-8")


def _read_spec_text(spec_path: Path) -> str:
    return spec_path.read_text(encoding="utf-8", errors="ignore")[:12000]


def run_swarm(args: Any) -> int:
    repo_root = Path(args.repo).resolve()
    artifacts = create_swarm_artifacts(repo_root)

    spec_record, spec_path = resolve_spec(
        repo_root=repo_root,
        provided_spec=args.spec,
        goal=args.goal,
        gen_if_missing=bool(args.gen_spec_if_missing),
    )
    write_resolution_record(artifacts.run_dir / "spec_resolution.json", spec_record)

    if spec_path is None:
        print(NO_SPEC_MESSAGE)
        summary = {
            "status": "fail",
            "mode": "run",
            "reason": "missing_spec",
            "exit_code": 2,
            "run_dir": _to_rel(artifacts.run_dir, repo_root),
        }
        write_json(artifacts.run_dir / "summary.json", summary)
        copy_or_replace_dir(artifacts.run_dir, artifacts.latest_dir)
        return 2

    experts, planner_note = _resolve_selection(
        repo_root=repo_root,
        goal=args.goal,
        max_experts=args.max_experts,
        planner_augmentation=bool(args.planner_augmentation),
        codex_bin=args.codex_bin,
        timeout_sec=args.codex_timeout_sec,
    )

    plan_payload = {
        "schema_version": "1.0",
        "mode": "run",
        "goal": args.goal,
        "spec": _to_rel(spec_path, repo_root),
        "experts": experts,
        "safety_rules": GLOBAL_SAFETY_RULES,
        "budgets": {
            "max_iterations": args.max_iterations,
            "max_experts": args.max_experts,
            "time_budget_sec": args.time_budget,
            "max_diff_lines": args.max_diff_lines,
        },
        "planner_note": planner_note,
        "autofix": bool(args.autofix),
        "dry_run": bool(args.dry_run),
        "created_at": utc_now_iso(),
    }
    write_json(artifacts.run_dir / "plan.json", plan_payload)

    dry_run = bool(args.dry_run)
    integration_dir: Path | None = None
    base_ref = "HEAD"

    if not dry_run:
        integration_dir, base_ref, integration_error = _prepare_integration_worktree(repo_root, artifacts.run_dir)
        if integration_error:
            summary = {
                "status": "fail",
                "mode": "run",
                "reason": "integration_worktree_error",
                "detail": integration_error,
                "run_dir": _to_rel(artifacts.run_dir, repo_root),
            }
            write_json(artifacts.run_dir / "summary.json", summary)
            copy_or_replace_dir(artifacts.run_dir, artifacts.latest_dir)
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 1

    run_started = time.monotonic()
    max_iterations = int(args.max_iterations if args.autofix else 1)
    routed_experts = experts
    iteration_records: list[dict[str, Any]] = []
    final_status = "fail"
    last_gate_payload: dict[str, Any] | None = None

    for iteration in range(1, max_iterations + 1):
        elapsed = time.monotonic() - run_started
        if elapsed > int(args.time_budget):
            iteration_records.append(
                {
                    "iteration": iteration,
                    "status": "fail",
                    "reason": "time_budget_exceeded",
                    "elapsed_sec": round(elapsed, 3),
                }
            )
            final_status = "fail"
            break

        spec_text = _read_spec_text(spec_path)

        # Security first pass.
        security_assignments = _build_assignments(["SecurityExpert"], args.goal, _to_rel(spec_path, repo_root))
        security_results = asyncio.run(
            executor.execute_assignments(
                assignments=security_assignments,
                repo_root=integration_dir or repo_root,
                base_ref=base_ref,
                artifacts=artifacts,
                codex_bin=args.codex_bin,
                timeout_sec=args.codex_timeout_sec,
                batch_id=f"iter{iteration:02d}_security_pre",
                spec_text=spec_text,
                dry_run=dry_run,
            )
        )

        run_experts = [expert for expert in routed_experts if expert != "SecurityExpert"]
        mid_assignments = _build_assignments(run_experts, args.goal, _to_rel(spec_path, repo_root))
        mid_results = asyncio.run(
            executor.execute_assignments(
                assignments=mid_assignments,
                repo_root=integration_dir or repo_root,
                base_ref=base_ref,
                artifacts=artifacts,
                codex_bin=args.codex_bin,
                timeout_sec=args.codex_timeout_sec,
                batch_id=f"iter{iteration:02d}_parallel",
                spec_text=spec_text,
                dry_run=dry_run,
            )
        )

        # Security final pass.
        security_final_assignments = _build_assignments(["SecurityExpert"], args.goal, _to_rel(spec_path, repo_root))
        security_final_results = asyncio.run(
            executor.execute_assignments(
                assignments=security_final_assignments,
                repo_root=integration_dir or repo_root,
                base_ref=base_ref,
                artifacts=artifacts,
                codex_bin=args.codex_bin,
                timeout_sec=args.codex_timeout_sec,
                batch_id=f"iter{iteration:02d}_security_final",
                spec_text=spec_text,
                dry_run=dry_run,
            )
        )

        all_results: list[ExpertResult] = security_results + mid_results + security_final_results

        write_json(
            artifacts.run_dir / "assignments.json",
            {
                "iteration": iteration,
                "assignments": _summarize_assignment_rows(
                    security_assignments + mid_assignments + security_final_assignments
                ),
            },
        )

        integration_outcome = integrator.merge_expert_results(
            results=all_results,
            max_diff_lines=int(args.max_diff_lines),
            apply_patch=(lambda result: (True, "dry-run")) if dry_run else _apply_patch_into_integration(integration_dir),
        )

        conflicts_payload = [asdict(item) for item in integration_outcome.conflicts]
        write_json(
            artifacts.run_dir / f"integration_iter_{iteration:02d}.json",
            {
                "status": integration_outcome.status,
                "applied": integration_outcome.applied,
                "skipped": integration_outcome.skipped,
                "conflicts": conflicts_payload,
                "diff_lines": integration_outcome.diff_lines,
            },
        )

        if integration_outcome.status == "fail":
            iteration_records.append(
                {
                    "iteration": iteration,
                    "status": "fail",
                    "reason": "max_diff_lines_exceeded",
                    "diff_lines": integration_outcome.diff_lines,
                }
            )
            final_status = "fail"
            break

        if not dry_run and integration_dir is not None:
            commit_sha = _commit_integration_if_dirty(integration_dir, iteration)
            if commit_sha:
                base_ref = _refresh_base_ref(integration_dir)

        if dry_run:
            gate_status, gate_payload, gate_text = _simulate_gate(iteration)
        else:
            assert integration_dir is not None
            gate_status, gate_payload, gate_text = _run_pipeline_gate(integration_dir, spec_path)
        gate_payload = gate_payload or {"overall_status": gate_status, "steps": []}
        last_gate_payload = gate_payload
        _write_gate_iteration(artifacts, iteration, gate_payload, gate_text)

        record: dict[str, Any] = {
            "iteration": iteration,
            "gate_status": gate_status,
            "integration_status": integration_outcome.status,
            "applied_experts": integration_outcome.applied,
            "conflicts": conflicts_payload,
            "diff_lines": integration_outcome.diff_lines,
        }
        route_payload: dict[str, Any] | None = None

        if gate_status == "pass":
            iteration_records.append(record)
            final_status = "pass"
            break

        route = routing.classify_and_route(
            pipeline_result=gate_payload,
            gate_report_text=gate_text,
            max_experts=args.max_experts,
        )
        route_payload = {
            "reason": route.reason,
            "failing_steps": route.failing_steps,
            "experts": route.experts,
        }
        record["routing"] = route_payload
        iteration_records.append(record)
        if integration_outcome.conflicts:
            routed_experts = _lead_conflict_experts(conflicts_payload, args.max_experts)
        else:
            routed_experts = route.experts

    summary = {
        "status": final_status,
        "mode": "run",
        "goal": args.goal,
        "spec": _to_rel(spec_path, repo_root),
        "run_dir": _to_rel(artifacts.run_dir, repo_root),
        "iterations": iteration_records,
        "iterations_completed": len(iteration_records),
        "max_iterations": max_iterations,
        "planner_note": planner_note,
        "last_gate_status": (last_gate_payload or {}).get("overall_status"),
        "ended_at": utc_now_iso(),
    }
    write_json(artifacts.run_dir / "summary.json", summary)
    copy_or_replace_dir(artifacts.run_dir, artifacts.latest_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if final_status == "pass" else 1


def run_gen_spec(args: Any) -> int:
    from swarm_skills.swarm.spec_resolution import generate_spec

    repo_root = Path(args.repo).resolve()
    generated = generate_spec(repo_root, args.goal)
    summary = {
        "status": "pass",
        "mode": "gen-spec",
        "repo": repo_root.as_posix(),
        "generated_spec": _to_rel(generated, repo_root),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_arg_namespace(raw: dict[str, Any]) -> Any:
    return SimpleNamespace(**raw)
