from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swarm_skills.catalog import TemplateMetadata, load_templates
from swarm_skills.runtime import SkillRun, write_json


CONSTRAINT_KEYS = ("auth", "crud", "realtime", "seo")
KEYWORDS = {
    "auth": ("auth", "login", "signin", "sign-in", "account"),
    "crud": ("crud", "create", "update", "delete", "edit", "list"),
    "realtime": ("realtime", "real-time", "websocket", "live updates"),
    "seo": ("seo", "metadata", "indexable", "search engine"),
}
PREFERENCE_KEYS = {
    "nextjs": ("next.js", "nextjs"),
    "prisma": ("prisma",),
    "sqlite": ("sqlite",),
    "postgres": ("postgres", "postgresql"),
}


@dataclass(frozen=True)
class TemplateScore:
    template: TemplateMetadata
    score: int
    rationale: list[str]


def infer_constraints(spec_text: str) -> dict[str, bool]:
    text = spec_text.lower()
    inferred: dict[str, bool] = {}
    for key, tokens in KEYWORDS.items():
        inferred[key] = any(token in text for token in tokens)
    return inferred


def infer_preferences(spec_text: str) -> dict[str, bool]:
    text = spec_text.lower()
    inferred: dict[str, bool] = {}
    for key, tokens in PREFERENCE_KEYS.items():
        inferred[key] = any(token in text for token in tokens)
    return inferred


def score_template(
    template: TemplateMetadata,
    required: dict[str, bool],
    preferences: dict[str, bool] | None = None,
) -> TemplateScore:
    score = 0
    rationale: list[str] = []
    prefs = preferences or {}

    if template.status == "active":
        score += 15
        rationale.append("active template")
    else:
        score -= 15
        rationale.append("stub template")

    if template.is_bootable:
        score += 10
        rationale.append("boot command available")
    else:
        score -= 20
        rationale.append("no boot command")

    if template.capabilities.get("persistence", False):
        score += 2
        rationale.append("has persistence layer")

    for key, expected in required.items():
        actual = bool(template.capabilities.get(key, False))
        if expected and actual:
            score += 4
            rationale.append(f"matches required {key}")
        elif expected and not actual:
            score -= 60
            rationale.append(f"missing required {key}")
        elif not expected and not actual:
            score += 1

    if template.risk_flags:
        score -= len(template.risk_flags)

    if prefs.get("nextjs") and template.capabilities.get("framework_nextjs", False):
        score += 3
        rationale.append("matches nextjs preference")
    if prefs.get("prisma") and template.capabilities.get("orm_prisma", False):
        score += 3
        rationale.append("matches prisma preference")
    if prefs.get("sqlite") and "sqlite" in " ".join(template.risk_flags + (template.description.lower(),)):
        score += 1
        rationale.append("aligns with sqlite preference")
    if prefs.get("postgres") and "postgres" in template.description.lower():
        score += 1
        rationale.append("aligns with postgres preference")

    return TemplateScore(template=template, score=score, rationale=rationale)


def choose_template(
    templates: list[TemplateMetadata],
    required: dict[str, bool],
    preferences: dict[str, bool] | None = None,
) -> TemplateScore:
    ranked = [score_template(template, required, preferences=preferences) for template in templates]
    ranked.sort(key=lambda item: (-item.score, item.template.id))
    return ranked[0]


def _render_runbook(template: TemplateMetadata, required: dict[str, bool]) -> str:
    install_steps = template.runbook.get("install_steps", [])
    run_steps = template.runbook.get("run_steps", [])
    env_vars = template.runbook.get("env", [])

    lines = [
        "# Runbook",
        "",
        f"Template: `{template.id}`",
        f"Status: `{template.status}`",
        "",
        "## Required Constraints",
        "",
    ]
    if required:
        for key in CONSTRAINT_KEYS:
            if key in required:
                lines.append(f"- `{key}`: `{required[key]}`")
    else:
        lines.append("- None explicitly required; safest default was selected.")

    lines.extend(["", "## Install Steps", ""])
    if install_steps:
        for step in install_steps:
            lines.append(f"1. `{step}`")
    else:
        lines.append("1. No install step required.")

    lines.extend(["", "## Run Steps", ""])
    if run_steps:
        for step in run_steps:
            lines.append(f"1. `{step}`")
    else:
        lines.append("1. No run steps available for this template.")

    lines.extend(["", "## Environment Variables", ""])
    if env_vars:
        for env in env_vars:
            lines.append(f"- `{env}`")
    else:
        lines.append("- None")

    return "\n".join(lines) + "\n"


def run(args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    spec_path = (workspace_root / args.spec).resolve()

    skill_run = SkillRun(skill="template_select", workspace_root=workspace_root)

    if not spec_path.exists():
        skill_run.add_note(f"SPEC not found: {spec_path}")
        return skill_run.finalize("fail", emit_json=args.json)

    templates = load_templates(workspace_root)
    if not templates:
        skill_run.add_note("No templates found under templates/*/template.json")
        return skill_run.finalize("fail", emit_json=args.json)

    spec_text = spec_path.read_text(encoding="utf-8")
    inferred = infer_constraints(spec_text)
    preferences = infer_preferences(spec_text)

    required: dict[str, bool] = {}
    for key in CONSTRAINT_KEYS:
        explicit = getattr(args, key)
        if explicit is not None:
            required[key] = explicit
        elif inferred.get(key):
            required[key] = True

    # Deterministic safety override: if inferred constraints point only to stub templates,
    # prefer an active bootable baseline and record the trade-off.
    selected = choose_template(templates, required, preferences=preferences)
    if selected.template.status != "active":
        fallback = choose_template(templates, {}, preferences=preferences)
        if fallback.template.status == "active":
            selected = fallback
            skill_run.add_note(
                "Selected safest active template because constraints mapped to non-bootable stubs."
            )

    ambiguous = len(required) == 0

    candidate_scores = [score_template(template, required, preferences=preferences) for template in templates]
    candidate_scores.sort(key=lambda item: (-item.score, item.template.id))

    choice_payload = {
        "ambiguity": {
            "is_ambiguous": ambiguous,
            "resolution": "safest default" if ambiguous else "constraints-driven",
        },
        "constraints": {
            "explicit": {key: getattr(args, key) for key in CONSTRAINT_KEYS},
            "inferred_from_spec": inferred,
            "preferences_inferred_from_spec": preferences,
            "required_for_selection": required,
        },
        "ranked_candidates": [
            {
                "id": scored.template.id,
                "version": scored.template.version,
                "name": scored.template.name,
                "path": str(scored.template.path.relative_to(workspace_root)),
                "rationale": scored.rationale,
                "risk_flags": list(scored.template.risk_flags),
                "score": scored.score,
                "status": scored.template.status,
            }
            for scored in candidate_scores
        ],
        "selected_template": {
            "id": selected.template.id,
            "version": selected.template.version,
            "name": selected.template.name,
            "path": str(selected.template.path.relative_to(workspace_root)),
            "rationale": selected.rationale,
            "risk_flags": list(selected.template.risk_flags),
            "status": selected.template.status,
        },
        "spec_path": str(spec_path.relative_to(workspace_root)),
    }

    runbook_text = _render_runbook(selected.template, required)

    choice_path = skill_run.run_dir / "template_choice.json"
    runbook_path = skill_run.run_dir / "runbook.md"
    write_json(choice_path, choice_payload)
    runbook_path.write_text(runbook_text, encoding="utf-8")
    skill_run.record_artifact(choice_path)
    skill_run.record_artifact(runbook_path)

    compat_plan_dir = workspace_root / "artifacts" / "plan"
    compat_plan_dir.mkdir(parents=True, exist_ok=True)
    compat_choice = compat_plan_dir / "template_choice.json"
    compat_runbook = compat_plan_dir / "runbook.md"
    write_json(compat_choice, choice_payload)
    compat_runbook.write_text(runbook_text, encoding="utf-8")
    skill_run.record_artifact(compat_choice)
    skill_run.record_artifact(compat_runbook)

    if ambiguous:
        skill_run.add_note("Constraints were ambiguous; selected safest default and recorded rationale.")

    skill_run.add_note(f"Selected template: {selected.template.id}")
    return skill_run.finalize(
        "pass",
        emit_json=args.json,
        provenance={
            "template_id": selected.template.id,
            "template_version": selected.template.version,
        },
    )
