from __future__ import annotations

import argparse

from swarm_skills.swarm.runner import run_gen_spec, run_plan, run_swarm


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default=".", help="Repository root (default: current directory)")
    parser.add_argument("--goal", required=True, help="High-level implementation goal")
    parser.add_argument("--spec", required=False, help="Absolute override path to SPEC markdown")
    parser.add_argument(
        "--gen-spec-if-missing",
        action="store_true",
        help="Generate spec and continue when no spec is found",
    )
    parser.add_argument("--max-iterations", type=int, default=3, help="Maximum swarm retry iterations")
    parser.add_argument("--max-experts", type=int, default=6, help="Maximum experts to schedule")
    parser.add_argument("--time-budget", type=int, default=1800, help="Time budget in seconds")
    parser.add_argument("--max-diff-lines", type=int, default=1200, help="Maximum merged diff lines")
    parser.add_argument(
        "--planner-augmentation",
        action="store_true",
        help="Allow Codex planner to augment baseline expert selection",
    )
    parser.add_argument("--codex-bin", default="codex", help="Codex CLI binary name/path")
    parser.add_argument("--codex-timeout-sec", type=int, default=900, help="Per-expert Codex timeout")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-swarm",
        description="Codex-only multi-agent team-mode swarm runner",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run full team-mode swarm loop")
    _add_common(run_parser)
    run_parser.add_argument("--autofix", action="store_true", help="Enable multi-iteration retry loop")
    run_parser.add_argument("--dry-run", action="store_true", help="Simulate experts without invoking Codex")

    plan_parser = subparsers.add_parser("plan", help="Generate swarm plan and assignments only")
    _add_common(plan_parser)
    plan_parser.add_argument("--dry-run", action="store_true", help="Accepted for compatibility; ignored in plan mode")
    plan_parser.add_argument("--autofix", action="store_true", help="Accepted for compatibility; ignored in plan mode")

    gen_spec_parser = subparsers.add_parser("gen-spec", help="Generate a SPEC and exit")
    gen_spec_parser.add_argument("--repo", default=".", help="Repository root")
    gen_spec_parser.add_argument("--goal", required=True, help="High-level implementation goal")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return run_swarm(args)
    if args.command == "plan":
        return run_plan(args)
    if args.command == "gen-spec":
        return run_gen_spec(args)

    parser.error(f"Unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
