from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from swarm_skills.runtime import SkillRun, write_json

SCHEMA_VERSION = "1.0"


CLASSES = [
    "env/bootstrap",
    "contract mismatch",
    "backend runtime",
    "frontend binding",
    "db persistence",
    "test flakiness",
]


def _keyword_score(text: str, keywords: list[str]) -> int:
    lowered = text.lower()
    return sum(1 for token in keywords if token in lowered)


def _classify(content: str) -> tuple[str, dict[str, int]]:
    scores = {
        "env/bootstrap": _keyword_score(content, ["dependency", "permission denied", "bootstrap", "not on path", "tooling"]),
        "contract mismatch": _keyword_score(content, ["contract", "missing required endpoint", "mapping", "api_contract", "acceptance"]),
        "backend runtime": _keyword_score(content, ["server", "exception", "traceback", "runtime", "route"]),
        "frontend binding": _keyword_score(content, ["route", "mock data", "frontend", "linked endpoint", "api usage"]),
        "db persistence": _keyword_score(content, ["db", "sqlite", "persistence", "row", "constraint"]),
        "test flakiness": _keyword_score(content, ["flaky", "timed out", "intermittent", "retry"]),
    }
    label = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return label, scores


def _load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _collect_structured_context(artifacts_root: Path) -> dict[str, Any]:
    files = {
        "backend_coverage": artifacts_root / "backend" / "latest" / "contract_coverage.json",
        "frontend_api_usage": artifacts_root / "frontend" / "latest" / "api_usage.json",
        "frontend_mock_report": artifacts_root / "frontend" / "latest" / "mock_data_report.json",
        "tests_api": artifacts_root / "tests" / "latest" / "api_results.json",
        "tests_db": artifacts_root / "tests" / "latest" / "db_results.json",
        "tests_summary": artifacts_root / "tests" / "latest" / "summary.json",
        "tests_ui": artifacts_root / "tests" / "latest" / "ui_results.json",
    }
    context: dict[str, Any] = {}
    for key, path in files.items():
        payload = _load_json_if_exists(path)
        if payload is not None:
            context[key] = payload
    return context


def _classify_from_backend_coverage(context: dict[str, Any]) -> tuple[str, list[str]] | None:
    coverage = context.get("backend_coverage")
    if not isinstance(coverage, dict):
        return None
    missing_required = coverage.get("missing_required", [])
    mismatched_methods = coverage.get("mismatched_methods", [])
    mismatched_paths = coverage.get("mismatched_paths", [])
    if missing_required or mismatched_methods or mismatched_paths:
        evidence = [
            f"missing_required={len(missing_required)}",
            f"mismatched_methods={len(mismatched_methods)}",
            f"mismatched_paths={len(mismatched_paths)}",
        ]
        return "contract mismatch", evidence
    return None


def _classify_from_frontend_signals(context: dict[str, Any]) -> tuple[str, list[str]] | None:
    mock_report = context.get("frontend_mock_report")
    api_usage = context.get("frontend_api_usage")
    if isinstance(mock_report, dict) and mock_report.get("findings"):
        return "frontend binding", [f"mock_findings={len(mock_report.get('findings', []))}"]
    if isinstance(api_usage, dict):
        route_usage = api_usage.get("route_usage", [])
        if isinstance(route_usage, list):
            unlinked = [
                row.get("route")
                for row in route_usage
                if isinstance(row, dict) and not row.get("linked_endpoints")
            ]
            if unlinked:
                return "frontend binding", [f"unlinked_routes={','.join(map(str, unlinked[:5]))}"]
    return None


def _failed_tests(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    tests = payload.get("tests", [])
    if not isinstance(tests, list):
        return []
    return [row for row in tests if isinstance(row, dict) and row.get("status") == "failed"]


def _classify_from_test_results(context: dict[str, Any]) -> tuple[str, list[str]] | None:
    db_failed = _failed_tests(context.get("tests_db"))
    if db_failed:
        return "db persistence", [f"db_failed={len(db_failed)}"]

    api_failed = _failed_tests(context.get("tests_api"))
    if api_failed:
        texts = " ".join(
            str(row.get("error") or row.get("stderr") or row.get("detail") or "")
            for row in api_failed
        ).lower()
        if any(token in texts for token in ["flaky", "intermittent", "retry", "timed out", "timeout"]):
            return "test flakiness", [f"api_failed={len(api_failed)}", "detected_flaky_signal=true"]
        return "backend runtime", [f"api_failed={len(api_failed)}"]

    ui_failed = _failed_tests(context.get("tests_ui"))
    if ui_failed:
        return "frontend binding", [f"ui_failed={len(ui_failed)}"]
    return None


def _likely_files(label: str) -> list[str]:
    mapping = {
        "env/bootstrap": [
            "scripts/skills/README.md",
            "scripts/demo_fullstack_mvp.sh",
            "swarm_skills/commands/doctor.py",
        ],
        "contract mismatch": [
            "artifacts/contracts/latest/API_CONTRACT.md",
            "artifacts/contracts/latest/api_contract.json",
            "swarm_skills/commands/plan_to_contracts.py",
            "swarm_skills/commands/backend_build.py",
        ],
        "backend runtime": [
            "templates/local-node-http-crud/server.js",
            "templates/local-node-http-crud/lib/todo_store.js",
            "swarm_skills/commands/backend_build.py",
        ],
        "frontend binding": [
            "swarm_skills/commands/frontend_bind.py",
            "artifacts/frontend/latest/api_usage.json",
            "artifacts/frontend/latest/mock_data_report.json",
        ],
        "db persistence": [
            "templates/local-node-http-crud/data/todos.json",
            "templates/local-node-http-crud/lib/todo_store.js",
            "swarm_skills/commands/fullstack_test_harness.py",
        ],
        "test flakiness": [
            "tests/test_fullstack_test_harness.py",
            "tests/test_plan_to_contracts.py",
            "swarm_skills/commands/fullstack_test_harness.py",
        ],
    }
    return mapping.get(label, [])


def _build_rerun_recipe(label: str) -> list[str]:
    if label == "contract mismatch":
        return [
            "python -m skills plan_to_contracts --spec examples/SPEC.todo.md",
            "python -m skills backend_build --contracts artifacts/contracts/latest/api_contract.json",
            "python -m skills fullstack_test_harness",
            "python -m skills pipeline --spec examples/SPEC.todo.md",
        ]
    if label == "frontend binding":
        return [
            "python -m skills frontend_bind --contracts-dir artifacts/contracts/latest",
            "python -m skills fullstack_test_harness",
            "python -m skills pipeline --spec examples/SPEC.todo.md",
        ]
    if label == "db persistence":
        return [
            "python -m skills fullstack_test_harness",
            "python -m skills pipeline --spec examples/SPEC.todo.md",
        ]
    if label == "backend runtime":
        return [
            "python -m skills backend_build --contracts artifacts/contracts/latest/api_contract.json",
            "python -m skills fullstack_test_harness",
            "python -m skills pipeline --spec examples/SPEC.todo.md",
        ]
    return [
        "python -m skills fullstack_test_harness",
        "python -m skills pipeline --spec examples/SPEC.todo.md",
    ]


def run(args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    skill_run = SkillRun(skill="triage_and_patch", workspace_root=workspace_root, artifact_dir_name="triage")

    gate_reports = [Path(item).resolve() if Path(item).is_absolute() else (workspace_root / item).resolve() for item in (args.gate_report or [])]
    if not gate_reports:
        skill_run.add_note("No gate reports provided.")
        return skill_run.finalize("fail", emit_json=args.json)

    missing = [path for path in gate_reports if not path.exists()]
    if missing:
        skill_run.add_note(f"Missing gate report(s): {', '.join(str(item) for item in missing)}")
        return skill_run.finalize("fail", emit_json=args.json)

    artifacts_root = (workspace_root / args.artifacts_root).resolve() if args.artifacts_root else (workspace_root / "artifacts")
    contracts_root = (workspace_root / args.contracts).resolve() if args.contracts else (workspace_root / "artifacts" / "contracts" / "latest")

    report_texts = []
    for path in gate_reports:
        report_texts.append(f"# Source: {path}\n" + path.read_text(encoding="utf-8", errors="ignore"))
    combined = "\n\n".join(report_texts)

    structured_context = _collect_structured_context(artifacts_root)
    evidence_lines: list[str] = []
    source_used = "text_fallback"
    classification = _classify_from_backend_coverage(structured_context)
    if classification is None:
        classification = _classify_from_frontend_signals(structured_context)
        if classification is not None:
            source_used = "frontend_signals"
    else:
        source_used = "backend_coverage"
    if classification is None:
        classification = _classify_from_test_results(structured_context)
        if classification is not None:
            source_used = "test_results"

    combined_with_context = combined + "\n\n" + json.dumps(structured_context, sort_keys=True)
    if classification is None:
        label, scores = _classify(combined_with_context)
    else:
        label, evidence_lines = classification
        _, scores = _classify(combined_with_context)
    likely_targets = _likely_files(label)
    rerun_recipe = _build_rerun_recipe(label)

    root_cause_lines = [
        "# Root Cause Analysis",
        "",
        f"Classification: `{label}`",
        f"Signal source: `{source_used}`",
        "",
        "Evidence scores:",
    ]
    for key in sorted(scores):
        root_cause_lines.append(f"- {key}: {scores[key]}")
    if evidence_lines:
        root_cause_lines.extend(["", "Structured evidence:"])
        for line in evidence_lines:
            root_cause_lines.append(f"- {line}")
    root_cause_lines.extend(["", "Evidence excerpts:"])
    excerpt = combined_with_context[:1600]
    root_cause_lines.append("```text")
    root_cause_lines.append(excerpt)
    root_cause_lines.append("```")

    patch_lines = [
        "# Patch Plan",
        "",
        f"Primary class: `{label}`",
        "",
        "Minimal change set:",
    ]
    for file_path in likely_targets:
        patch_lines.append(f"- Review and patch `{file_path}`")

    patch_lines.extend(
        [
            "",
            "Rerun instructions:",
            "Execute the following commands in order:",
        ]
    )
    for index, command in enumerate(rerun_recipe, start=1):
        patch_lines.append(f"{index}. `{command}`")

    root_cause_path = skill_run.run_dir / "root_cause.md"
    patch_plan_path = skill_run.run_dir / "patch_plan.md"
    summary_path = skill_run.run_dir / "summary_payload.json"

    root_cause_path.write_text("\n".join(root_cause_lines) + "\n", encoding="utf-8")
    patch_plan_path.write_text("\n".join(patch_lines) + "\n", encoding="utf-8")

    payload = {
        "artifacts_root": str(artifacts_root.relative_to(workspace_root)) if artifacts_root.is_relative_to(workspace_root) else str(artifacts_root),
        "classification": label,
        "contracts_root": str(contracts_root.relative_to(workspace_root)) if contracts_root.is_relative_to(workspace_root) else str(contracts_root),
        "gate_reports": [str(path.relative_to(workspace_root)) if path.is_relative_to(workspace_root) else str(path) for path in gate_reports],
        "likely_targets": likely_targets,
        "rerun_recipe": rerun_recipe,
        "scores": scores,
        "schema_version": SCHEMA_VERSION,
        "signal_source": source_used,
    }
    write_json(summary_path, payload)

    for path in [root_cause_path, patch_plan_path, summary_path]:
        skill_run.record_artifact(path)

    skill_run.add_note(f"Classified failure as: {label}")
    return skill_run.finalize(
        "pass",
        emit_json=args.json,
        summary_updates={"schema_version": SCHEMA_VERSION},
    )
