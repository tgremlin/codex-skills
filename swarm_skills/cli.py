from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

from swarm_skills.commands import doctor, scaffold_verify, template_select
from swarm_skills.commands.stub import run_stub
from swarm_skills.registry import RegistryData, RegistrySkill, load_registry, registry_as_json
from swarm_skills.spec_discovery import SpecDiscoveryError, discover_spec

try:
    from swarm_skills.commands import (
        backend_build,
        bench,
        frontend_bind,
        fullstack_test_harness,
        matrix,
        pipeline,
        plan_to_contracts,
        prune_artifacts,
        spec_wizard,
        template_check,
        triage_and_patch,
    )
except ImportError:
    backend_build = None
    bench = None
    frontend_bind = None
    fullstack_test_harness = None
    matrix = None
    pipeline = None
    plan_to_contracts = None
    prune_artifacts = None
    spec_wizard = None
    template_check = None
    triage_and_patch = None


CommandHandler = Callable[[argparse.Namespace], int]

INPUT_FLAG_MAP = {
    "contracts": "contracts",
    "gate_report": "gate_report",
    "logs": "logs",
    "repo": "repo",
    "spec": "spec",
    "template": "template",
    "test_plan": "test_plan",
}


def _add_workspace_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace-root",
        default=".",
        help="Workspace root for templates/artifacts (default: current directory)",
    )


def _add_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print skill summary JSON to stdout in addition to writing artifacts.",
    )


def _add_orchestrator_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--orchestrator",
        action="store_true",
        help="Machine mode: stdout emits one JSON object only; human logs go to stderr.",
    )


def _add_optional_bool(parser: argparse.ArgumentParser, flag: str, help_text: str) -> None:
    parser.add_argument(
        f"--{flag}",
        dest=flag,
        action=argparse.BooleanOptionalAction,
        default=None,
        help=help_text,
    )


def _build_registry_help(skill: RegistrySkill) -> str:
    required_flags = [f"--{INPUT_FLAG_MAP[item].replace('_', '-')}" for item in skill.required_inputs if item in INPUT_FLAG_MAP]
    lines = [
        f"Registry status: {skill.status}",
        f"Required flags: {', '.join(required_flags) if required_flags else '(none)'}",
        "Produced artifacts:",
    ]
    for artifact in skill.produced_artifacts:
        lines.append(f"- {artifact}")
    if "spec" in skill.required_inputs:
        lines.extend(
            [
                "",
                "Spec auto-discovery:",
                "- If --spec is omitted, CLI checks .swarm/spec_path.txt then .swarm/spec.json.",
                "- If no pointer exists, CLI searches common locations under examples/specs/.",
            ]
        )
    return "\n".join(lines)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    _add_workspace_arg(parser)
    _add_json_arg(parser)
    _add_orchestrator_arg(parser)


def _configure_parser_for_skill(parser: argparse.ArgumentParser, skill_id: str) -> None:
    _add_common_args(parser)
    if skill_id == "template_select":
        parser.add_argument("--spec", required=False, help="Path to SPEC markdown")
        _add_optional_bool(parser, "auth", "Require authentication support")
        _add_optional_bool(parser, "crud", "Require CRUD support")
        _add_optional_bool(parser, "realtime", "Require realtime support")
        _add_optional_bool(parser, "seo", "Require SEO support")
    elif skill_id == "scaffold_verify":
        parser.add_argument("--template", required=False, help="Template id or template path")
        parser.add_argument(
            "--port",
            default="auto",
            help="Port override for boot verification (integer or 'auto').",
        )
        parser.add_argument(
            "--health-timeout-sec",
            type=int,
            default=15,
            help="Health check timeout in seconds",
        )
    elif skill_id == "plan_to_contracts":
        parser.add_argument("--spec", required=False, help="Path to SPEC markdown")
        parser.add_argument(
            "--test-plan-source",
            default=None,
            help="Optional TEST_PLAN.md source; if provided it is validated and reused.",
        )
    elif skill_id == "backend_build":
        parser.add_argument(
            "--contracts",
            required=False,
            default="artifacts/contracts/latest/api_contract.json",
            help="Path to api_contract.json",
        )
        parser.add_argument("--template", required=False, help="Template id or path")
        parser.add_argument("--backend-root", required=False, help="Backend root path override")
    elif skill_id == "frontend_bind":
        parser.add_argument("--template", required=False, help="Template id or path")
        parser.add_argument("--contracts-dir", required=False, help="Contracts directory path")
        parser.add_argument("--frontend-root", required=False, help="Frontend root path override")
        parser.add_argument("--allowlist-config", required=False, help="Allowlist config JSON")
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Fail when expired exemptions are detected.",
        )
    elif skill_id == "fullstack_test_harness":
        parser.add_argument("--test-plan", required=False, help="Path to TEST_PLAN.md")
        parser.add_argument("--template", required=False, help="Template id or path")
        parser.add_argument("--network", action="store_true", help="Run network mode HTTP checks")
        parser.add_argument(
            "--health-timeout-sec",
            type=int,
            default=15,
            help="Health check timeout in seconds",
        )
    elif skill_id == "triage_and_patch":
        parser.add_argument(
            "--gate-report",
            action="append",
            default=[],
            help="Path to failing GateReport. Repeat flag for multiple reports.",
        )
        parser.add_argument("--logs", required=False, help="Path to logs bundle")
        parser.add_argument("--artifacts-root", required=False, help="Artifacts root path")
        parser.add_argument("--contracts", required=False, help="Contracts root path")
    elif skill_id == "pipeline":
        parser.add_argument("--spec", required=False, help="Path to SPEC markdown")
        parser.add_argument("--template", required=False, help="Template id or path override")
        parser.add_argument("--network", action="store_true", help="Pass --network to S6")
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Enable stricter policy checks (currently propagates to frontend_bind).",
        )
        parser.add_argument(
            "--stop-on-fail",
            dest="stop_on_fail",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Stop pipeline on first failure (default true)",
        )
        parser.add_argument(
            "--steps",
            required=False,
            help="Comma-separated step ids. Default: template_select,scaffold_verify,plan_to_contracts,backend_build,frontend_bind,fullstack_test_harness",
        )
        parser.add_argument(
            "--triage-on-fail",
            action="store_true",
            help="Invoke triage_and_patch automatically when pipeline fails.",
        )
    elif skill_id == "template_check":
        parser.add_argument("--template", required=False, help="Template id or template path")
        parser.add_argument("--all", action="store_true", help="Check all templates under templates/*")
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Escalate recommended-field warnings to failures.",
        )
    elif skill_id == "bench":
        parser.add_argument(
            "--spec-dir",
            default="examples/specs",
            help="Directory containing benchmark spec markdown files.",
        )
        parser.add_argument("--template", required=False, help="Optional forced template id/path")
        parser.add_argument("--network", action="store_true", help="Pass --network to pipeline")
        parser.add_argument("--strict", action="store_true", help="Pass --strict to pipeline")
        parser.add_argument(
            "--append-history",
            action="store_true",
            help="Append summary results to artifacts/bench/history.jsonl",
        )
    elif skill_id == "matrix":
        parser.add_argument(
            "--spec-dir",
            default="examples/specs",
            help="Directory containing benchmark spec markdown files.",
        )
        parser.add_argument(
            "--templates",
            default="all",
            help="Template ids comma list or 'all'.",
        )
        parser.add_argument("--network", action="store_true", help="Pass --network to pipeline")
        parser.add_argument("--strict", action="store_true", help="Pass --strict to pipeline")
        parser.add_argument(
            "--limit",
            type=int,
            default=12,
            help="Maximum number of spec/template combinations to run.",
        )
    elif skill_id == "prune_artifacts":
        parser.add_argument(
            "--keep-days",
            type=int,
            default=14,
            help="Delete timestamped artifact runs older than this many days.",
        )
        parser.add_argument(
            "--keep-latest",
            dest="keep_latest",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Retained for compatibility; latest artifacts are always preserved.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report deletions without deleting files.")
        parser.add_argument(
            "--skills",
            required=False,
            help="Comma-separated artifact skill directories to prune (default: all under artifacts/).",
        )
    elif skill_id == "spec_wizard":
        parser.add_argument("--repo", required=False, help="Target application repository path")
        parser.add_argument(
            "--out",
            required=False,
            help="Output SPEC markdown path (default: examples/specs/<app>_wizard.md)",
        )
        parser.add_argument("--app-name", required=False, help="Application name override")
        parser.add_argument(
            "--flow-next",
            action="store_true",
            help="If target repo has .flow state, import Flow-Next epics/tasks.",
        )
        parser.add_argument(
            "--epic",
            required=False,
            help="Flow-Next epic id filter (comma-separated allowed).",
        )
        parser.add_argument(
            "--run-contracts",
            action="store_true",
            help="Run plan_to_contracts after spec generation.",
        )
        parser.add_argument(
            "--run-pipeline",
            action="store_true",
            help="Run pipeline --triage-on-fail after spec generation.",
        )
        parser.add_argument(
            "--non-interactive",
            action="store_true",
            help="Disable prompts and load wizard answers from --answers JSON.",
        )
        parser.add_argument(
            "--answers",
            required=False,
            help="Path to JSON answers file (required with --non-interactive).",
        )


def _build_handlers() -> dict[str, CommandHandler]:
    handlers: dict[str, CommandHandler] = {
        "doctor": doctor.run,
        "template_select": template_select.run,
        "scaffold_verify": scaffold_verify.run,
    }
    if plan_to_contracts is not None:
        handlers["plan_to_contracts"] = plan_to_contracts.run
    else:
        handlers["plan_to_contracts"] = lambda args: run_stub("plan_to_contracts", args)
    if fullstack_test_harness is not None:
        handlers["fullstack_test_harness"] = fullstack_test_harness.run
    else:
        handlers["fullstack_test_harness"] = lambda args: run_stub("fullstack_test_harness", args)
    if backend_build is not None:
        handlers["backend_build"] = backend_build.run
    else:
        handlers["backend_build"] = lambda args: run_stub("backend_build", args)
    if frontend_bind is not None:
        handlers["frontend_bind"] = frontend_bind.run
    else:
        handlers["frontend_bind"] = lambda args: run_stub("frontend_bind", args)
    if triage_and_patch is not None:
        handlers["triage_and_patch"] = triage_and_patch.run
    else:
        handlers["triage_and_patch"] = lambda args: run_stub("triage_and_patch", args)
    if pipeline is not None:
        handlers["pipeline"] = pipeline.run
    else:
        handlers["pipeline"] = lambda args: run_stub("pipeline", args)
    if template_check is not None:
        handlers["template_check"] = template_check.run
    else:
        handlers["template_check"] = lambda args: run_stub("template_check", args)
    if bench is not None:
        handlers["bench"] = bench.run
    else:
        handlers["bench"] = lambda args: run_stub("bench", args)
    if matrix is not None:
        handlers["matrix"] = matrix.run
    else:
        handlers["matrix"] = lambda args: run_stub("matrix", args)
    if prune_artifacts is not None:
        handlers["prune_artifacts"] = prune_artifacts.run
    else:
        handlers["prune_artifacts"] = lambda args: run_stub("prune_artifacts", args)
    if spec_wizard is not None:
        handlers["spec_wizard"] = spec_wizard.run
    else:
        handlers["spec_wizard"] = lambda args: run_stub("spec_wizard", args)
    return handlers


def _validate_required_inputs(args: argparse.Namespace, registry_skill: RegistrySkill) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for required in registry_skill.required_inputs:
        arg_name = INPUT_FLAG_MAP.get(required)
        if arg_name is None:
            continue
        value = getattr(args, arg_name, None)
        is_empty_collection = isinstance(value, (list, tuple, set, dict)) and len(value) == 0
        if value in (None, "") or is_empty_collection:
            missing.append(f"--{arg_name.replace('_', '-')}")
    return (len(missing) == 0, missing)


def _build_parser(registry: RegistryData) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m skills",
        description="Full-Stack App Swarm Skills Pack CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List commands from registry.json")
    list_parser.add_argument("--json", action="store_true", help="Print registry as machine-readable JSON")
    list_parser.add_argument(
        "--orchestrator",
        action="store_true",
        help="Machine mode: stdout emits one JSON object only.",
    )
    list_parser.set_defaults(command="list")

    for skill in registry.skills:
        skill_parser = subparsers.add_parser(skill.id, help=skill.cli, epilog=_build_registry_help(skill))
        _configure_parser_for_skill(skill_parser, skill.id)

    return parser


def _orchestrator_output_path(workspace_root: Path, command: str) -> Path:
    mapping = {
        "backend_build": workspace_root / "artifacts" / "backend" / "latest" / "summary.json",
        "bench": workspace_root / "artifacts" / "bench" / "latest" / "summary.json",
        "doctor": workspace_root / "artifacts" / "doctor" / "latest" / "summary.json",
        "frontend_bind": workspace_root / "artifacts" / "frontend" / "latest" / "summary.json",
        "fullstack_test_harness": workspace_root / "artifacts" / "tests" / "latest" / "summary.json",
        "matrix": workspace_root / "artifacts" / "matrix" / "latest" / "summary.json",
        "pipeline": workspace_root / "artifacts" / "pipeline" / "latest" / "pipeline_result.json",
        "plan_to_contracts": workspace_root / "artifacts" / "contracts" / "latest" / "summary.json",
        "prune_artifacts": workspace_root / "artifacts" / "prune" / "latest" / "summary.json",
        "scaffold_verify": workspace_root / "artifacts" / "scaffold_verify" / "latest" / "summary.json",
        "spec_wizard": workspace_root / "artifacts" / "spec_wizard" / "latest" / "summary.json",
        "template_check": workspace_root / "artifacts" / "template_check" / "latest" / "summary.json",
        "template_select": workspace_root / "artifacts" / "template_select" / "latest" / "summary.json",
        "triage_and_patch": workspace_root / "artifacts" / "triage" / "latest" / "summary.json",
    }
    return mapping.get(command, workspace_root / "artifacts" / command / "latest" / "summary.json")


def _emit_orchestrator_json(workspace_root: Path, command: str, exit_code: int) -> int:
    payload_path = _orchestrator_output_path(workspace_root, command)
    if payload_path.exists():
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {
                "message": "Expected machine output exists but is invalid JSON.",
                "output_path": str(payload_path),
                "status": "fail",
            }
            exit_code = 1
    else:
        payload = {
            "message": "Expected machine output is missing.",
            "output_path": str(payload_path),
            "status": "fail",
        }
        exit_code = 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


def _spec_discovery_payload(
    *,
    command: str,
    workspace_root: Path,
    error: SpecDiscoveryError,
) -> dict[str, object]:
    candidates: list[str] = []
    for candidate in sorted(error.candidates, key=lambda item: item.resolve().as_posix()):
        resolved = candidate.resolve()
        if resolved.is_relative_to(workspace_root):
            candidates.append(str(resolved.relative_to(workspace_root)).replace("\\", "/"))
        else:
            candidates.append(str(resolved).replace("\\", "/"))
    return {
        "schema_version": "1.0",
        "status": "fail",
        "command": command,
        "error_type": "spec_discovery_error",
        "reason": error.reason,
        "detail": error.detail,
        "candidates": candidates,
        "guidance": error.guidance,
    }


def _maybe_discover_spec(
    *,
    args: argparse.Namespace,
    registry_skill: RegistrySkill,
    workspace_root: Path,
) -> SpecDiscoveryError | None:
    requires_spec = "spec" in registry_skill.required_inputs and hasattr(args, "spec")
    if not requires_spec:
        return None
    if getattr(args, "spec", None):
        return None
    try:
        discovered = discover_spec(workspace_root)
    except SpecDiscoveryError as exc:
        return exc
    args.spec = str(discovered.resolve())
    return None


def main(argv: list[str] | None = None) -> int:
    registry = load_registry()
    parser = _build_parser(registry)
    args = parser.parse_args(argv)

    if args.command == "list":
        if args.orchestrator or args.json:
            print(json.dumps(registry_as_json(registry), indent=2, sort_keys=True))
        else:
            for skill in registry.skills:
                print(f"{skill.id}: {skill.cli}")
        return 0

    registry_skill = next((item for item in registry.skills if item.id == args.command), None)
    if registry_skill is None:
        parser.error(f"Command '{args.command}' is not declared in registry.json")

    workspace_root = Path(getattr(args, "workspace_root", ".")).resolve()
    discovery_error = _maybe_discover_spec(args=args, registry_skill=registry_skill, workspace_root=workspace_root)
    if discovery_error is not None:
        payload = _spec_discovery_payload(
            command=args.command,
            workspace_root=workspace_root,
            error=discovery_error,
        )
        if getattr(args, "orchestrator", False) or getattr(args, "json", False):
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"[skills] spec discovery failed for `{args.command}`", file=sys.stderr)
            print(f"reason: {payload['reason']}", file=sys.stderr)
            print(f"detail: {payload['detail']}", file=sys.stderr)
            if payload["candidates"]:
                print("candidates:", file=sys.stderr)
                for candidate in payload["candidates"]:
                    print(f"- {candidate}", file=sys.stderr)
            print(f"guidance: {payload['guidance']}", file=sys.stderr)
        return 1

    ok, missing_flags = _validate_required_inputs(args, registry_skill)
    if not ok:
        parser.error(
            "Missing required inputs from registry.json: " + ", ".join(sorted(missing_flags))
        )

    handlers = _build_handlers()
    handler = handlers.get(args.command)
    if handler is None:
        parser.error(f"No handler wired for command '{args.command}'")

    if not hasattr(args, "orchestrator"):
        return handler(args)

    if args.orchestrator:
        if hasattr(args, "json"):
            args.json = False
        print(f"[skills] orchestrator mode running `{args.command}`", file=sys.stderr)
        with contextlib.redirect_stdout(sys.stderr):
            exit_code = handler(args)
        return _emit_orchestrator_json(workspace_root=workspace_root, command=args.command, exit_code=exit_code)

    return handler(args)
