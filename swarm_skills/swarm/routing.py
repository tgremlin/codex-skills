from __future__ import annotations

from typing import Any

from swarm_skills.swarm.models import GateRouting, required_experts


_STEP_TO_EXPERTS = {
    "template_select": ["DevOpsExpert", "BackendExpert"],
    "scaffold_verify": ["DevOpsExpert", "BackendExpert"],
    "plan_to_contracts": ["BackendExpert", "DBExpert"],
    "backend_build": ["BackendExpert", "DBExpert"],
    "frontend_bind": ["FrontendExpert"],
    "fullstack_test_harness": ["TestingExpert", "BackendExpert", "FrontendExpert", "DBExpert"],
}


_KEYWORD_TO_EXPERT = [
    ("frontend", "FrontendExpert"),
    ("ui", "FrontendExpert"),
    ("route", "BackendExpert"),
    ("db", "DBExpert"),
    ("migration", "DBExpert"),
    ("docker", "DevOpsExpert"),
    ("k8s", "DevOpsExpert"),
    ("docs", "DocsExpert"),
]


def classify_and_route(
    *,
    pipeline_result: dict[str, Any] | None,
    gate_report_text: str,
    max_experts: int,
) -> GateRouting:
    failing_steps: list[str] = []
    routed: list[str] = []

    if isinstance(pipeline_result, dict):
        steps = pipeline_result.get("steps", [])
        if isinstance(steps, list):
            for row in steps:
                if not isinstance(row, dict):
                    continue
                if row.get("status") == "fail":
                    step_name = str(row.get("step_name") or "")
                    if step_name:
                        failing_steps.append(step_name)
                        for expert in _STEP_TO_EXPERTS.get(step_name, []):
                            if expert not in routed:
                                routed.append(expert)

    lowered = gate_report_text.lower()
    for keyword, expert in _KEYWORD_TO_EXPERT:
        if keyword in lowered and expert not in routed:
            routed.append(expert)

    reason = "pipeline_failure"
    if not failing_steps and not routed:
        reason = "unknown_failure"

    final_experts: list[str] = []
    for expert in list(required_experts()) + routed:
        if expert not in final_experts:
            final_experts.append(expert)

    if max_experts < len(required_experts()):
        max_experts = len(required_experts())

    return GateRouting(
        reason=reason,
        failing_steps=sorted(set(failing_steps)),
        experts=final_experts[:max_experts],
    )
