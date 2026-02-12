from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExpertDefinition:
    name: str
    role_prompt: str
    allowed_paths: tuple[str, ...]


@dataclass
class ExpertAssignment:
    expert: str
    role_prompt: str
    task: str
    allowed_paths: list[str]
    required_output_schema: dict[str, Any]
    prompt_path: str | None = None


@dataclass
class ExpertResult:
    expert: str
    status: str
    summary: str
    changed_files: list[str] = field(default_factory=list)
    patch_path: str | None = None
    transcript_path: str | None = None
    diff_line_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MergeConflict:
    expert: str
    reason: str
    files: list[str] = field(default_factory=list)


@dataclass
class IntegrationOutcome:
    status: str
    applied: list[str]
    conflicts: list[MergeConflict]
    skipped: list[str]
    diff_lines: int


@dataclass
class GateRouting:
    reason: str
    failing_steps: list[str]
    experts: list[str]


@dataclass
class SpecResolutionRecord:
    provided_spec: str | None
    discovered_candidates: list[str]
    chosen_spec: str | None
    generated_spec: str | None
    mode: str


@dataclass
class SwarmArtifacts:
    repo_root: Path
    run_dir: Path
    latest_dir: Path
    patches_dir: Path
    transcripts_dir: Path
    gate_reports_dir: Path


_REQUIRED_EXPERTS = ("SecurityExpert", "TestingExpert")


def required_experts() -> tuple[str, str]:
    return _REQUIRED_EXPERTS
