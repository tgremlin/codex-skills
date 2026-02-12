from __future__ import annotations

from swarm_skills.swarm.routing import classify_and_route


def test_routing_maps_failed_steps_to_experts() -> None:
    payload = {
        "steps": [
            {"step_name": "frontend_bind", "status": "fail"},
            {"step_name": "backend_build", "status": "pass"},
        ]
    }

    route = classify_and_route(
        pipeline_result=payload,
        gate_report_text="frontend route missing",
        max_experts=6,
    )

    assert route.reason == "pipeline_failure"
    assert "frontend_bind" in route.failing_steps
    assert route.experts[0] == "SecurityExpert"
    assert route.experts[1] == "TestingExpert"
    assert "FrontendExpert" in route.experts


def test_routing_fallback_keeps_required_experts() -> None:
    route = classify_and_route(
        pipeline_result=None,
        gate_report_text="unknown crash",
        max_experts=6,
    )

    assert route.reason == "unknown_failure"
    assert route.experts[:2] == ["SecurityExpert", "TestingExpert"]
