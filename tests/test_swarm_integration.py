from __future__ import annotations

from swarm_skills.swarm.integrator import merge_expert_results
from swarm_skills.swarm.models import ExpertResult


def test_merge_prefers_non_overlapping_changes() -> None:
    seen: list[str] = []

    def apply_patch(result: ExpertResult) -> tuple[bool, str]:
        seen.append(result.expert)
        return True, "ok"

    results = [
        ExpertResult(
            expert="BackendExpert",
            status="pass",
            summary="ok",
            changed_files=["swarm_skills/a.py"],
            patch_path="/tmp/a.patch",
            diff_line_count=10,
        ),
        ExpertResult(
            expert="FrontendExpert",
            status="pass",
            summary="ok",
            changed_files=["swarm_skills/b.py"],
            patch_path="/tmp/b.patch",
            diff_line_count=12,
        ),
    ]

    outcome = merge_expert_results(results=results, max_diff_lines=100, apply_patch=apply_patch)

    assert outcome.status == "pass"
    assert set(outcome.applied) == {"BackendExpert", "FrontendExpert"}
    assert not outcome.conflicts
    assert set(seen) == {"BackendExpert", "FrontendExpert"}


def test_merge_routes_overlaps_as_conflicts() -> None:
    def apply_patch(_: ExpertResult) -> tuple[bool, str]:
        return True, "ok"

    results = [
        ExpertResult(
            expert="BackendExpert",
            status="pass",
            summary="ok",
            changed_files=["swarm_skills/a.py"],
            patch_path="/tmp/a.patch",
            diff_line_count=10,
        ),
        ExpertResult(
            expert="TestingExpert",
            status="pass",
            summary="ok",
            changed_files=["swarm_skills/a.py"],
            patch_path="/tmp/t.patch",
            diff_line_count=8,
        ),
    ]

    outcome = merge_expert_results(results=results, max_diff_lines=100, apply_patch=apply_patch)

    assert outcome.status == "conflict"
    assert len(outcome.conflicts) == 1
    assert outcome.conflicts[0].reason == "overlapping_changes"


def test_merge_fails_when_diff_budget_exceeded() -> None:
    def apply_patch(_: ExpertResult) -> tuple[bool, str]:
        return True, "ok"

    results = [
        ExpertResult(
            expert="BackendExpert",
            status="pass",
            summary="ok",
            changed_files=["swarm_skills/a.py"],
            patch_path="/tmp/a.patch",
            diff_line_count=150,
        )
    ]

    outcome = merge_expert_results(results=results, max_diff_lines=100, apply_patch=apply_patch)

    assert outcome.status == "fail"
    assert outcome.diff_lines == 150
