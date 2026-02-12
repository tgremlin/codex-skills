from __future__ import annotations

import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from swarm_skills.runtime import SkillRun, write_json

SCHEMA_VERSION = "1.0"
TIMESTAMP_RE = re.compile(r"^\d{8}T\d{6}Z$")
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def _parse_timestamp_dir(name: str) -> datetime | None:
    if not TIMESTAMP_RE.match(name):
        return None
    try:
        return datetime.strptime(name, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _target_skill_dirs(artifacts_root: Path, skills_raw: str | None) -> list[Path]:
    if skills_raw:
        names = sorted({item.strip() for item in skills_raw.split(",") if item.strip()})
        return [artifacts_root / name for name in names]
    return sorted(path for path in artifacts_root.iterdir() if path.is_dir()) if artifacts_root.exists() else []


def _rel(path: Path, workspace_root: Path) -> str:
    return str(path.relative_to(workspace_root)) if path.is_relative_to(workspace_root) else str(path)


def run(args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    keep_days = int(getattr(args, "keep_days", 14))
    keep_latest = bool(getattr(args, "keep_latest", True))
    dry_run = bool(getattr(args, "dry_run", False))
    skills_raw = getattr(args, "skills", None)

    skill_run = SkillRun(skill="prune_artifacts", workspace_root=workspace_root, artifact_dir_name="prune")

    if keep_days < 0:
        skill_run.add_note("Invalid --keep-days value; must be >= 0.")
        return skill_run.finalize("fail", emit_json=args.json)

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=keep_days)
    artifacts_root = workspace_root / "artifacts"

    deleted: list[str] = []
    would_delete: list[str] = []
    kept: list[str] = []
    errors: list[str] = []

    skill_dirs = _target_skill_dirs(artifacts_root, skills_raw)

    if not keep_latest:
        skill_run.add_note("--no-keep-latest requested, but policy keeps artifacts/**/latest/** preserved.")

    for skill_dir in skill_dirs:
        if not skill_dir.exists() or not skill_dir.is_dir():
            continue
        for child in sorted(skill_dir.iterdir(), key=lambda item: item.name):
            if child.name == "latest":
                kept.append(_rel(child, workspace_root))
                continue
            if skill_dir.name == "bench" and child.name == "history.jsonl":
                kept.append(_rel(child, workspace_root))
                continue
            if not child.is_dir():
                continue

            child_ts = _parse_timestamp_dir(child.name)
            if child_ts is None:
                continue
            if child_ts >= cutoff:
                kept.append(_rel(child, workspace_root))
                continue

            rel_path = _rel(child, workspace_root)
            if dry_run:
                would_delete.append(rel_path)
                continue
            try:
                shutil.rmtree(child)
                deleted.append(rel_path)
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{rel_path}: {exc}")

    report = {
        "cutoff_utc": cutoff.replace(microsecond=0).isoformat(),
        "deleted": sorted(deleted),
        "dry_run": dry_run,
        "errors": sorted(errors),
        "keep_days": keep_days,
        "kept": sorted(set(kept)),
        "schema_version": SCHEMA_VERSION,
        "skills": [path.name for path in skill_dirs],
        "would_delete": sorted(would_delete),
    }

    report_path = skill_run.run_dir / "prune_report.json"
    write_json(report_path, report)
    skill_run.record_artifact(report_path)

    status = "fail" if errors else "pass"
    gate_lines = [
        "# Prune Artifacts GateReport",
        "",
        f"Status: {status.upper()}",
        f"Dry run: `{dry_run}`",
        f"Keep days: `{keep_days}`",
        "",
        f"Deleted runs: {len(deleted)}",
        f"Would delete runs: {len(would_delete)}",
        f"Errors: {len(errors)}",
        "",
        "Preserved by policy:",
        "- `artifacts/**/latest/**`",
        "- `artifacts/bench/history.jsonl`",
    ]

    if errors:
        gate_lines.extend(["", "Errors:"])
        for item in sorted(errors):
            gate_lines.append(f"- {item}")

    gate_lines.extend(
        [
            "",
            "Next fix steps:",
            "1. Re-run with `--dry-run` to preview changes before deletion.",
            "2. Verify retained artifacts in `artifacts/*/latest/`.",
            "3. Re-run prune on a schedule (for example daily in CI maintenance).",
        ]
    )

    gate_path = skill_run.run_dir / "GateReport.md"
    gate_path.write_text("\n".join(gate_lines) + "\n", encoding="utf-8")
    skill_run.record_artifact(gate_path)

    if errors:
        skill_run.add_note("Prune encountered deletion errors.")
    elif dry_run:
        skill_run.add_note("Dry-run completed; no artifact directories deleted.")
    else:
        skill_run.add_note("Artifact pruning completed.")

    return skill_run.finalize(
        status,
        emit_json=args.json,
        summary_updates={
            "deleted_count": len(deleted),
            "dry_run": dry_run,
            "errors_count": len(errors),
            "keep_days": keep_days,
            "would_delete_count": len(would_delete),
        },
    )
