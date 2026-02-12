from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from swarm_skills.runtime import copy_or_replace_dir, utc_timestamp, write_json
from swarm_skills.swarm.models import SpecResolutionRecord


class MissingSpecError(Exception):
    pass


def _to_rel(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    root = repo_root.resolve()
    if resolved.is_relative_to(root):
        return resolved.relative_to(root).as_posix()
    return resolved.as_posix()


def _glob_files(root: Path, pattern: str) -> list[Path]:
    found = []
    for path in sorted(root.glob(pattern), key=lambda item: item.as_posix()):
        if path.exists() and path.is_file():
            found.append(path.resolve())
    return found


def discover_spec_candidates(repo_root: Path) -> list[Path]:
    repo_root = repo_root.resolve()
    candidate_map: dict[str, Path] = {}

    ordered_groups = [
        _glob_files(repo_root, "artifacts/flow_next_spec/latest/*.md"),
        _glob_files(repo_root, "examples/specs/*.md"),
        _glob_files(repo_root, "docs/specs/*.md"),
        _glob_files(repo_root, "*spec*.md") + _glob_files(repo_root, "*requirements*.md"),
    ]

    for group in ordered_groups:
        for path in group:
            rel = _to_rel(path, repo_root)
            candidate_map[rel] = path

    return [candidate_map[key] for key in sorted(candidate_map)]


def choose_newest(candidates: list[Path]) -> Path | None:
    if not candidates:
        return None

    ranked = sorted(
        candidates,
        key=lambda path: (-path.stat().st_mtime_ns, path.resolve().as_posix()),
    )
    return ranked[0]


def _render_generated_spec(goal: str) -> str:
    safe_goal = goal.strip() or "Implement the requested feature."
    return (
        "# Generated SPEC\n\n"
        "## Goal\n"
        f"- {safe_goal}\n\n"
        "## Scope\n"
        "- Implement a deterministic, testable change set for the goal.\n"
        "- Preserve existing behavior outside the requested scope.\n\n"
        "## Non-goals\n"
        "- Refactoring unrelated modules.\n"
        "- Adding non-deterministic runtime behavior.\n\n"
        "## Acceptance Criteria\n"
        "- [ ] Required functionality is implemented.\n"
        "- [ ] Existing gates pass after integration.\n"
        "- [ ] Security and testing checks are explicitly handled.\n\n"
        "## TEST_PLAN\n"
        "1. Run deterministic pipeline gates.\n"
        "2. Validate touched paths and artifact outputs.\n"
    )


def generate_spec(repo_root: Path, goal: str) -> Path:
    repo_root = repo_root.resolve()
    ts = utc_timestamp()
    run_dir = repo_root / "artifacts" / "flow_next_spec" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    spec_path = run_dir / "generated_spec.md"
    spec_path.write_text(_render_generated_spec(goal), encoding="utf-8")

    latest_dir = repo_root / "artifacts" / "flow_next_spec" / "latest"
    copy_or_replace_dir(run_dir, latest_dir)

    metadata = {
        "generated_spec": _to_rel(spec_path, repo_root),
        "goal": goal,
        "timestamp": ts,
    }
    write_json(run_dir / "summary.json", metadata)
    return spec_path.resolve()


def resolve_spec(
    *,
    repo_root: Path,
    provided_spec: str | None,
    goal: str,
    gen_if_missing: bool,
) -> tuple[SpecResolutionRecord, Path | None]:
    repo_root = repo_root.resolve()

    if provided_spec:
        path = Path(provided_spec).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Provided --spec does not exist: {path}")
        record = SpecResolutionRecord(
            provided_spec=path.as_posix(),
            discovered_candidates=[],
            chosen_spec=path.as_posix(),
            generated_spec=None,
            mode="provided",
        )
        return record, path

    candidates = discover_spec_candidates(repo_root)
    candidates_rel = [_to_rel(path, repo_root) for path in candidates]

    chosen = choose_newest(candidates)
    if chosen is not None:
        record = SpecResolutionRecord(
            provided_spec=None,
            discovered_candidates=candidates_rel,
            chosen_spec=_to_rel(chosen, repo_root),
            generated_spec=None,
            mode="discovered",
        )
        return record, chosen

    if gen_if_missing:
        generated = generate_spec(repo_root, goal)
        record = SpecResolutionRecord(
            provided_spec=None,
            discovered_candidates=[],
            chosen_spec=_to_rel(generated, repo_root),
            generated_spec=_to_rel(generated, repo_root),
            mode="generated",
        )
        return record, generated

    record = SpecResolutionRecord(
        provided_spec=None,
        discovered_candidates=[],
        chosen_spec=None,
        generated_spec=None,
        mode="missing_error",
    )
    return record, None


def write_resolution_record(path: Path, record: SpecResolutionRecord) -> None:
    write_json(path, asdict(record))
