from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from swarm_skills.swarm.models import required_experts
from swarm_skills.swarm.policy import EXPERT_DEFINITIONS


_OPTIONAL_EXPERT_ORDER = [
    "BackendExpert",
    "FrontendExpert",
    "DBExpert",
    "DevOpsExpert",
    "DocsExpert",
]


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def scan_repo(repo_root: Path) -> dict[str, bool]:
    repo_root = repo_root.resolve()
    package_json = _load_json(repo_root / "package.json") or {}
    deps: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        payload = package_json.get(section)
        if isinstance(payload, dict):
            for key, value in payload.items():
                deps[str(key).lower()] = str(value)

    has_frontend = any((repo_root / part).exists() for part in ["app", "pages", "components", "frontend", "src"])
    has_frontend = has_frontend or any(dep in deps for dep in ["next", "react", "vite", "@types/react"])

    has_backend = any((repo_root / part).exists() for part in ["api", "server", "backend", "swarm_skills"])
    has_backend = has_backend or any(dep in deps for dep in ["fastapi", "flask", "django", "express"])

    db_patterns = [
        "**/migrations/*.sql",
        "**/schema.prisma",
        "**/alembic.ini",
        "**/alembic/env.py",
        "**/db/*.sql",
    ]
    has_db = False
    for pattern in db_patterns:
        if any(repo_root.glob(pattern)):
            has_db = True
            break
    has_db = has_db or any(dep in deps for dep in ["prisma", "sqlalchemy", "psycopg", "psycopg2", "drizzle-orm"])

    has_devops = any((repo_root / part).exists() for part in ["Dockerfile", ".github", "docker", "helm", "k8s"])

    has_docs = any((repo_root / part).exists() for part in ["docs", "README.md", "CHANGELOG.md"])

    return {
        "backend": has_backend,
        "frontend": has_frontend,
        "db": has_db,
        "devops": has_devops,
        "docs": has_docs,
    }


def _include_docs_from_goal(goal: str) -> bool:
    lowered = goal.lower()
    tokens = ["docs", "documentation", "readme", "guide", "changelog"]
    return any(token in lowered for token in tokens)


def select_experts_deterministic(repo_root: Path, goal: str, max_experts: int) -> list[str]:
    scan = scan_repo(repo_root)
    selected = list(required_experts())

    if scan["backend"]:
        selected.append("BackendExpert")
    if scan["frontend"]:
        selected.append("FrontendExpert")
    if scan["db"]:
        selected.append("DBExpert")
    if scan["devops"]:
        selected.append("DevOpsExpert")
    if scan["docs"] and _include_docs_from_goal(goal):
        selected.append("DocsExpert")

    ordered: list[str] = []
    for expert in list(required_experts()) + _OPTIONAL_EXPERT_ORDER:
        if expert in selected and expert not in ordered:
            ordered.append(expert)

    if max_experts < len(required_experts()):
        max_experts = len(required_experts())

    return ordered[:max_experts]


def _extract_json_blob(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if not raw:
        return None

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                payload = json.loads(stripped)
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                continue

    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def planner_augment_experts(
    *,
    repo_root: Path,
    goal: str,
    current: list[str],
    codex_bin: str,
    timeout_sec: int,
) -> tuple[list[str], str | None]:
    prompt = (
        "Select additional specialists for this engineering task.\n"
        "Allowed additions: BackendExpert, FrontendExpert, DBExpert, DevOpsExpert, DocsExpert.\n"
        "Always preserve SecurityExpert and TestingExpert.\n"
        "Return ONLY compact JSON with schema: {\"add_experts\": [\"Name\", ...]}.\n\n"
        f"Goal: {goal}\n"
        f"Current experts: {', '.join(current)}\n"
    )

    cmd = [
        codex_bin,
        "exec",
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        "--cd",
        str(repo_root),
    ]

    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )

    if result.returncode != 0 and "unexpected argument '--ask-for-approval'" in (result.stderr or ""):
        fallback_cmd = [
            codex_bin,
            "exec",
            "--sandbox",
            "read-only",
            "--cd",
            str(repo_root),
        ]
        result = subprocess.run(
            fallback_cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )

    if result.returncode != 0:
        return current, f"planner_failed_exit_{result.returncode}"

    payload = _extract_json_blob(result.stdout or "")
    if not payload:
        return current, "planner_invalid_json"

    additions_raw = payload.get("add_experts", [])
    if not isinstance(additions_raw, list):
        return current, "planner_invalid_schema"

    allowed = set(_OPTIONAL_EXPERT_ORDER)
    additions: list[str] = []
    for item in additions_raw:
        name = str(item)
        if name in allowed and name in EXPERT_DEFINITIONS:
            additions.append(name)

    merged = list(current)
    for item in _OPTIONAL_EXPERT_ORDER:
        if item in additions and item not in merged:
            merged.append(item)

    return merged, None
