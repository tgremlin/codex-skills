from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from swarm_skills.swarm.models import ExpertResult, IntegrationOutcome, MergeConflict


ApplyPatchFn = Callable[[ExpertResult], tuple[bool, str]]


def merge_expert_results(
    *,
    results: list[ExpertResult],
    max_diff_lines: int,
    apply_patch: ApplyPatchFn,
) -> IntegrationOutcome:
    applied: list[str] = []
    skipped: list[str] = []
    conflicts: list[MergeConflict] = []
    touched_files: set[str] = set()
    diff_lines = 0

    for result in sorted(results, key=lambda item: item.expert):
        if result.status != "pass":
            skipped.append(result.expert)
            continue
        if not result.patch_path or not result.changed_files:
            skipped.append(result.expert)
            continue

        overlap = sorted(set(result.changed_files) & touched_files)
        if overlap:
            conflicts.append(
                MergeConflict(
                    expert=result.expert,
                    reason="overlapping_changes",
                    files=overlap,
                )
            )
            continue

        ok, detail = apply_patch(result)
        if not ok:
            conflicts.append(
                MergeConflict(
                    expert=result.expert,
                    reason=f"apply_failed:{detail}",
                    files=list(result.changed_files),
                )
            )
            continue

        applied.append(result.expert)
        touched_files.update(result.changed_files)
        diff_lines += int(result.diff_line_count)

    if diff_lines > max_diff_lines:
        return IntegrationOutcome(
            status="fail",
            applied=applied,
            conflicts=conflicts,
            skipped=skipped,
            diff_lines=diff_lines,
        )

    status = "pass" if not conflicts else "conflict"
    return IntegrationOutcome(
        status=status,
        applied=applied,
        conflicts=conflicts,
        skipped=skipped,
        diff_lines=diff_lines,
    )


def build_git_apply(repo_dir: Path) -> ApplyPatchFn:
    repo_dir = repo_dir.resolve()

    def _apply(result: ExpertResult) -> tuple[bool, str]:
        assert result.patch_path is not None
        patch_path = Path(result.patch_path)
        completed = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch_path)],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return True, "ok"
        return False, (completed.stderr or completed.stdout or "git apply failed").strip()

    return _apply
