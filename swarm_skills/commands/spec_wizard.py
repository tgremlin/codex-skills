from __future__ import annotations

import json
import re
import shlex
from argparse import Namespace
from pathlib import Path
from typing import Any

from swarm_skills.commands import pipeline, plan_to_contracts
from swarm_skills.runtime import SkillRun, run_command, write_json

SCHEMA_VERSION = "1.0"

_ALLOWED_LAYERS = {"ui", "api", "db"}
_ALLOWED_TEST_TYPES = {"unit", "integration", "e2e", "migration", "policy"}
_REQUIRED_HEADINGS = [
    "## Scope",
    "## Non-goals",
    "## Acceptance Criteria",
    "## Key Entities / Data Model Notes",
    "## Endpoints / Operations",
    "## TEST_PLAN",
]
_TEST_PLAN_COLUMNS = "| Test ID | Acceptance Criteria | Layer | Type | Description |"

_AUTH_CHOICES = [
    "none",
    "email+password",
    "magic link",
    "OAuth",
    "use Supabase Auth",
]

_STACK_PACKAGE_MAP: dict[str, tuple[str, ...]] = {
    "next": ("nextjs",),
    "react": ("react",),
    "express": ("express",),
    "fastapi": ("fastapi",),
    "django": ("django",),
    "flask": ("flask",),
    "@supabase/supabase-js": ("supabase", "supabase-js"),
    "@supabase/auth-helpers-nextjs": ("supabase", "supabase-auth-helpers"),
    "next-auth": ("next-auth",),
    "@clerk/nextjs": ("clerk",),
    "prisma": ("prisma",),
    "@prisma/client": ("prisma",),
    "drizzle-orm": ("drizzle",),
    "knex": ("knex",),
    "typeorm": ("typeorm",),
    "sqlalchemy": ("sqlalchemy",),
    "psycopg": ("postgres",),
    "psycopg2": ("postgres",),
}

_AUTH_SIGNAL_PACKAGES = {
    "next-auth",
    "@supabase/auth-helpers-nextjs",
    "@supabase/supabase-js",
    "@clerk/nextjs",
    "oauthlib",
}

_DATA_SIGNAL_PACKAGES = {
    "prisma",
    "@prisma/client",
    "drizzle-orm",
    "knex",
    "typeorm",
    "sqlalchemy",
    "psycopg",
    "psycopg2",
    "@supabase/supabase-js",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _to_rel(path: Path, root: Path) -> str:
    if path.is_relative_to(root):
        return str(path.relative_to(root)).replace("\\", "/")
    return str(path).replace("\\", "/")


def _sanitize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "app"


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _infer_app_name(repo_root: Path) -> str:
    package_json = _read_json(repo_root / "package.json") or {}
    package_name = package_json.get("name")
    if isinstance(package_name, str) and package_name.strip():
        return package_name.strip()

    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists() and pyproject.is_file():
        try:
            import tomllib

            payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project = payload.get("project", {})
            project_name = project.get("name")
            if isinstance(project_name, str) and project_name.strip():
                return project_name.strip()
        except Exception:
            pass

    return repo_root.name


def _collect_package_dependencies(repo_root: Path) -> dict[str, str]:
    deps: dict[str, str] = {}

    package_json = _read_json(repo_root / "package.json") or {}
    for section in ("dependencies", "devDependencies"):
        payload = package_json.get(section)
        if isinstance(payload, dict):
            for name, version in payload.items():
                if isinstance(name, str):
                    deps[name] = str(version)

    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists() and pyproject.is_file():
        try:
            import tomllib

            payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project = payload.get("project", {})
            py_deps = project.get("dependencies", [])
            if isinstance(py_deps, list):
                for item in py_deps:
                    if not isinstance(item, str):
                        continue
                    name = re.split(r"[<>=!~; ]", item, maxsplit=1)[0].strip()
                    if name:
                        deps[name] = item
            optional = project.get("optional-dependencies", {})
            if isinstance(optional, dict):
                for group in sorted(optional):
                    group_items = optional.get(group, [])
                    if isinstance(group_items, list):
                        for item in group_items:
                            if not isinstance(item, str):
                                continue
                            name = re.split(r"[<>=!~; ]", item, maxsplit=1)[0].strip()
                            if name:
                                deps[name] = item
        except Exception:
            pass

    for req_name in sorted(repo_root.glob("requirements*.txt")):
        if not req_name.is_file():
            continue
        for raw in req_name.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[<>=!~; ]", line, maxsplit=1)[0].strip()
            if name:
                deps[name] = line

    return dict(sorted(deps.items()))


def scan_repo(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    stack_evidence: dict[str, set[str]] = {}
    data_layer: dict[tuple[str, str], set[str]] = {}
    auth_indicators: set[str] = set()

    def add_stack(name: str, evidence: str) -> None:
        stack_evidence.setdefault(name, set()).add(evidence)

    def add_data_layer(layer_type: str, path: Path, evidence: str) -> None:
        rel = _to_rel(path, repo_root)
        data_layer.setdefault((layer_type, rel), set()).add(evidence)

    dependencies = _collect_package_dependencies(repo_root)

    for dep_name in sorted(dependencies):
        lowered = dep_name.lower()
        if lowered in _STACK_PACKAGE_MAP:
            for stack_name in _STACK_PACKAGE_MAP[lowered]:
                add_stack(stack_name, f"dependency:{dep_name}")
        if lowered in _AUTH_SIGNAL_PACKAGES:
            auth_indicators.add(f"dependency:{dep_name}")
        if lowered in _DATA_SIGNAL_PACKAGES:
            add_data_layer("package", repo_root / "package.json", f"dependency:{dep_name}")

    common_dirs = ["src", "app", "pages", "api", "server", "backend"]
    detected_entrypoints: list[str] = []
    for dirname in common_dirs:
        path = repo_root / dirname
        if path.exists() and path.is_dir():
            detected_entrypoints.append(_to_rel(path, repo_root))
            add_stack("repo-structure", f"dir:{dirname}")

    known_entrypoint_files = [
        "app/main.py",
        "app.py",
        "main.py",
        "server.js",
        "server.ts",
        "next.config.js",
        "next.config.mjs",
        "vite.config.ts",
        "middleware.ts",
        "middleware.js",
    ]
    for rel in known_entrypoint_files:
        file_path = repo_root / rel
        if file_path.exists() and file_path.is_file():
            detected_entrypoints.append(_to_rel(file_path, repo_root))
            add_stack("entrypoint", f"file:{rel}")

    for prisma_schema in sorted(repo_root.glob("**/schema.prisma")):
        if ".git" in prisma_schema.parts or "node_modules" in prisma_schema.parts:
            continue
        add_data_layer("prisma", prisma_schema, "schema.prisma")

    for rel in [
        "supabase/migrations",
        "migrations",
        "prisma/migrations",
        "db/migrations",
        "alembic",
        "drizzle",
    ]:
        path = repo_root / rel
        if path.exists() and path.is_dir():
            layer = "migrations" if "migrations" in rel else rel.split("/")[0]
            add_data_layer(layer, path, f"directory:{rel}")

    for rel in ["knexfile.js", "knexfile.ts", "drizzle.config.ts", "alembic.ini", "supabase/config.toml"]:
        path = repo_root / rel
        if path.exists() and path.is_file():
            layer = "config"
            if rel.startswith("supabase"):
                layer = "supabase"
            elif "knex" in rel:
                layer = "knex"
            elif "drizzle" in rel:
                layer = "drizzle"
            elif "alembic" in rel:
                layer = "alembic"
            add_data_layer(layer, path, f"file:{rel}")

    env_var_patterns: set[str] = set()
    for env_name in [".env.example", ".env.sample", ".env.template", "env.example"]:
        env_path = repo_root / env_name
        if not env_path.exists() or not env_path.is_file():
            continue
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^([A-Z][A-Z0-9_]+)=", line)
            if match:
                env_var_patterns.add(match.group(1))
        add_stack("env-template", f"file:{env_name}")

    if (repo_root / "supabase").exists():
        add_stack("supabase", "path:supabase/")
    if (repo_root / "package.json").exists() and "next" in dependencies:
        add_stack("nextjs", "package.json:next")

    if any(item.startswith("dependency:@supabase") or item.startswith("dependency:next-auth") or item.startswith("dependency:@clerk") for item in auth_indicators):
        add_stack("auth", "dependency-based-auth-detected")

    detected_stack = [
        {
            "name": stack_name,
            "evidence": sorted(stack_evidence[stack_name]),
        }
        for stack_name in sorted(stack_evidence)
    ]

    detected_data_layer = [
        {
            "type": layer_type,
            "path": path,
            "evidence": sorted(evidence),
        }
        for (layer_type, path), evidence in sorted(data_layer.items(), key=lambda item: (item[0][0], item[0][1]))
    ]

    unique_evidence = set()
    for item in detected_stack:
        unique_evidence.update(item["evidence"])
    for item in detected_data_layer:
        unique_evidence.update(item["evidence"])
    unique_evidence.update(f"entrypoint:{item}" for item in detected_entrypoints)
    unique_evidence.update(f"env:{item}" for item in env_var_patterns)

    evidence_count = len(unique_evidence)
    score = round(min(1.0, evidence_count / 16.0), 3)
    if score < 0.34:
        confidence_level = "low"
    elif score < 0.67:
        confidence_level = "medium"
    else:
        confidence_level = "high"

    return {
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(repo_root),
        "detected_stack": detected_stack,
        "detected_packages": sorted(dependencies.keys()),
        "detected_entrypoints": sorted(set(detected_entrypoints)),
        "detected_data_layer": detected_data_layer,
        "auth_indicators": sorted(auth_indicators),
        "env_var_patterns": sorted(env_var_patterns),
        "confidence": {
            "score": score,
            "level": confidence_level,
            "evidence_count": evidence_count,
            "method": "min(1.0, evidence_count/16.0)",
        },
    }


def _normalize_roles(value: Any) -> list[str]:
    if isinstance(value, list):
        roles = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        roles = _split_csv(value)
    else:
        roles = []
    if len(roles) < 2:
        return ["admin", "operator"]
    return roles[:4]


def _normalize_entities(value: Any) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    if isinstance(value, list):
        for row in value:
            if isinstance(row, dict):
                name = str(row.get("name", "")).strip()
                fields_raw = row.get("fields", [])
                if isinstance(fields_raw, str):
                    fields = [item.strip() for item in fields_raw.split(",") if item.strip()]
                elif isinstance(fields_raw, list):
                    fields = [str(item).strip() for item in fields_raw if str(item).strip()]
                else:
                    fields = []
                if name:
                    entities.append({"name": name, "fields": fields[:8]})
            elif isinstance(row, str):
                part = row.strip()
                if not part:
                    continue
                if ":" in part:
                    name, raw_fields = part.split(":", 1)
                    fields = [item.strip() for item in raw_fields.split(",") if item.strip()]
                else:
                    name = part
                    fields = []
                entities.append({"name": name.strip(), "fields": fields[:8]})
    elif isinstance(value, str):
        for chunk in [item.strip() for item in value.split(";") if item.strip()]:
            if ":" in chunk:
                name, raw_fields = chunk.split(":", 1)
                fields = [item.strip() for item in raw_fields.split(",") if item.strip()]
            else:
                name = chunk
                fields = []
            if name.strip():
                entities.append({"name": name.strip(), "fields": fields[:8]})

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entity in entities:
        key = entity["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(entity)
    return unique[:6]


def _normalize_operations(value: Any, roles: list[str]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    default_actor = roles[0] if roles else "user"

    def _from_text(text: str) -> dict[str, Any]:
        actor = default_actor
        name = text.strip()
        if ":" in text:
            actor_part, op_part = text.split(":", 1)
            if actor_part.strip() and op_part.strip():
                actor = actor_part.strip()
                name = op_part.strip()
        return {
            "name": name,
            "actor": actor,
            "inputs": "request payload",
            "output": "operation succeeds with updated state",
            "error_cases": "invalid input, unauthorized access",
            "notes": "",
        }

    if isinstance(value, list):
        for row in value:
            if isinstance(row, dict):
                name = str(row.get("name", "")).strip()
                if not name:
                    continue
                actor = str(row.get("actor", default_actor)).strip() or default_actor
                operations.append(
                    {
                        "name": name,
                        "actor": actor,
                        "inputs": str(row.get("inputs", "request payload")).strip() or "request payload",
                        "output": str(row.get("output", "operation succeeds with updated state")).strip()
                        or "operation succeeds with updated state",
                        "error_cases": str(row.get("error_cases", "invalid input, unauthorized access")).strip()
                        or "invalid input, unauthorized access",
                        "notes": str(row.get("notes", "")).strip(),
                    }
                )
            elif isinstance(row, str) and row.strip():
                operations.append(_from_text(row))
    elif isinstance(value, str):
        for chunk in [item.strip() for item in value.split(";") if item.strip()]:
            operations.append(_from_text(chunk))

    deduped: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for op in operations:
        key = op["name"].lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        deduped.append(op)

    return deduped[:12]


def _normalize_non_goals(value: Any) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        if "\n" in value:
            items = [line.strip(" -\t") for line in value.splitlines() if line.strip(" -\t")]
        else:
            items = [item.strip() for item in value.split(";") if item.strip()]
    else:
        items = []
    if not items:
        items = [
            "No redesign of unrelated product surfaces.",
            "No speculative integrations without repository evidence.",
            "No production secret handling changes in this spec generation step.",
        ]
    return items[:6]


def _default_entities_from_scan(scan: dict[str, Any]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    has_data = bool(scan.get("detected_data_layer"))
    if has_data:
        entities.append({"name": "Organization", "fields": ["id", "name", "created_at"]})
        entities.append({"name": "User", "fields": ["id", "email", "role"]})
        entities.append({"name": "Record", "fields": ["id", "status", "updated_at"]})
    else:
        entities.append({"name": "Record", "fields": ["id", "name", "status"]})
        entities.append({"name": "Session", "fields": ["id", "created_at", "expires_at"]})
    return entities[:6]


def _default_operations(entities: list[dict[str, Any]], roles: list[str]) -> list[dict[str, Any]]:
    actor = roles[0] if roles else "user"
    operations: list[dict[str, Any]] = []
    for entity in entities[:3]:
        name = entity["name"]
        operations.extend(
            [
                {
                    "name": f"List {name} records",
                    "actor": actor,
                    "inputs": "filters and pagination options",
                    "output": f"paginated {name} records",
                    "error_cases": "unauthorized access",
                    "notes": "",
                },
                {
                    "name": f"Create {name} record",
                    "actor": actor,
                    "inputs": f"required {name} fields",
                    "output": f"new {name} record id",
                    "error_cases": "validation failure, unauthorized access",
                    "notes": "",
                },
            ]
        )
    return operations[:8]


def _prompt(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    if raw:
        return raw
    return default or ""


def _collect_answers(
    *,
    app_name_hint: str,
    scan: dict[str, Any],
    args: Any,
) -> tuple[dict[str, Any], list[str]]:
    skipped_questions: list[str] = []
    answers_payload: dict[str, Any] = {}

    if getattr(args, "answers", None):
        answers_path = Path(args.answers).expanduser().resolve()
        if answers_path.exists() and answers_path.is_file():
            try:
                answers_payload = json.loads(answers_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                answers_payload = {}

    interactive = not bool(getattr(args, "non_interactive", False))

    app_name_raw = getattr(args, "app_name", None) or answers_payload.get("app_name")
    if not app_name_raw and interactive:
        app_name_raw = _prompt("App name", app_name_hint)
    if not app_name_raw:
        app_name_raw = app_name_hint
        skipped_questions.append("app_name")
    app_name = str(app_name_raw).strip() or app_name_hint

    roles_raw = answers_payload.get("roles")
    if roles_raw is None and interactive:
        roles_raw = _prompt("Primary user roles (2-4, comma separated)", "admin,operator")
    roles = _normalize_roles(roles_raw)
    if roles_raw is None:
        skipped_questions.append("roles")

    inferred_auth: str | None = None
    stack_names = {row.get("name", "") for row in scan.get("detected_stack", []) if isinstance(row, dict)}
    if "supabase" in stack_names:
        inferred_auth = "use Supabase Auth"
    elif "next-auth" in stack_names:
        inferred_auth = "OAuth"

    auth_raw = answers_payload.get("auth_requirement")
    if auth_raw is None and interactive:
        default_auth = inferred_auth or "none"
        auth_raw = _prompt(
            "Auth requirement (none, email+password, magic link, OAuth, use Supabase Auth)",
            default_auth,
        )
    if auth_raw is None:
        auth_raw = inferred_auth or "none"
        skipped_questions.append("auth_requirement")
    auth_requirement = str(auth_raw).strip()
    if auth_requirement not in _AUTH_CHOICES:
        auth_requirement = "none"

    entities_raw = answers_payload.get("entities")
    if entities_raw is None and interactive:
        entities_raw = _prompt(
            "Core entities (2-6). Format: Name:field1,field2; Name2:field1,field2",
            "",
        )
    entities = _normalize_entities(entities_raw)
    if not entities:
        entities = _default_entities_from_scan(scan)
        skipped_questions.append("entities")

    operations_raw = answers_payload.get("operations")
    if operations_raw is None and interactive:
        operations_raw = _prompt(
            "Core operations (5-12). Format: Actor:action; Actor:action",
            "",
        )
    operations = _normalize_operations(operations_raw, roles)
    if not operations:
        operations = _default_operations(entities, roles)
        skipped_questions.append("operations")

    non_goals_raw = answers_payload.get("non_goals")
    if non_goals_raw is None and interactive:
        non_goals_raw = _prompt("Non-goals (3-6 bullets; separate with ';')", "")
    non_goals = _normalize_non_goals(non_goals_raw)
    if non_goals_raw is None:
        skipped_questions.append("non_goals")

    dod_raw = answers_payload.get("definition_of_done")
    if dod_raw is None and interactive:
        dod_raw = _prompt("Definition of done (short)", "All ACs pass mapped tests and required gates")
    if dod_raw is None:
        skipped_questions.append("definition_of_done")
        definition_of_done = "All acceptance criteria map to executable tests and required gates pass."
    else:
        definition_of_done = str(dod_raw).strip() or "All acceptance criteria map to executable tests and required gates pass."

    return (
        {
            "app_name": app_name,
            "roles": roles,
            "auth_requirement": auth_requirement,
            "entities": entities,
            "operations": operations,
            "non_goals": non_goals,
            "definition_of_done": definition_of_done,
        },
        sorted(set(skipped_questions)),
    )


def _parse_flow_epics(text: str) -> list[dict[str, Any]]:
    epics: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        match = re.match(
            r"^\s*\[(?P<status>[^\]]+)\]\s+(?P<id>fn-[^:]+):\s+(?P<title>.*?)(?:\s+\((?P<done>\d+)\/(?P<total>\d+)\s+tasks\s+done\))?\s*$",
            line,
        )
        if not match:
            continue
        done = int(match.group("done")) if match.group("done") else None
        total = int(match.group("total")) if match.group("total") else None
        epics.append(
            {
                "id": match.group("id"),
                "title": match.group("title"),
                "status": match.group("status"),
                "done_tasks": done,
                "total_tasks": total,
            }
        )
    return sorted(epics, key=lambda item: item["id"])


def _parse_flow_tasks(text: str) -> list[dict[str, Any]]:
    status_map = {
        "done": "done",
        "todo": "todo",
        "in_progress": "doing",
    }
    tasks: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        match = re.match(r"^\s*\[(?P<status>done|todo|in_progress)\]\s+(?P<id>fn-[^:]+\.[0-9]+):\s+(?P<title>.+?)\s*$", line)
        if not match:
            continue
        task_id = match.group("id")
        epic_id = task_id.split(".")[0]
        title = re.sub(r"\s+\(deps:.*\)$", "", match.group("title")).strip()
        tasks.append(
            {
                "id": task_id,
                "epic_id": epic_id,
                "title": title,
                "status": status_map.get(match.group("status"), match.group("status")),
            }
        )
    return sorted(tasks, key=lambda item: (item["epic_id"], int(item["id"].split(".")[-1])))


def _default_active_epics(epics: list[dict[str, Any]]) -> list[str]:
    active = [
        epic
        for epic in epics
        if epic.get("total_tasks") is not None
        and epic.get("done_tasks") is not None
        and epic["done_tasks"] < epic["total_tasks"]
    ]
    if active:
        return [active[0]["id"]]
    return [epics[0]["id"]] if epics else []


def _flow_import(
    *,
    repo_root: Path,
    args: Any,
    answers_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    flow_summary: dict[str, Any] = {
        "enabled": bool(getattr(args, "flow_next", False)),
        "available": False,
        "validation": {
            "ok": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        },
        "selected_epics": [],
        "epics": [],
        "tasks": [],
    }
    warnings: list[str] = []

    if not flow_summary["enabled"]:
        return flow_summary, warnings

    flowctl = repo_root / ".flow" / "bin" / "flowctl"
    if not flowctl.exists() or not flowctl.is_file():
        warnings.append("Flow-Next requested but .flow/bin/flowctl is unavailable; continuing without Flow-Next import.")
        return flow_summary, warnings

    flow_summary["available"] = True

    validate_result = run_command([str(flowctl), "validate", "--all"], cwd=repo_root)
    flow_summary["validation"] = {
        "ok": validate_result.exit_code == 0,
        "exit_code": validate_result.exit_code,
        "stdout": validate_result.stdout.strip(),
        "stderr": validate_result.stderr.strip(),
    }
    if validate_result.exit_code != 0:
        warnings.append("Flow-Next validation failed; continuing with best-effort import.")

    epics_result = run_command([str(flowctl), "epics"], cwd=repo_root)
    tasks_result = run_command([str(flowctl), "tasks"], cwd=repo_root)

    if epics_result.exit_code != 0 or tasks_result.exit_code != 0:
        warnings.append("Flow-Next epics/tasks listing failed; continuing without imported tasks.")
        return flow_summary, warnings

    epics = _parse_flow_epics(epics_result.stdout)
    tasks = _parse_flow_tasks(tasks_result.stdout)

    selected_epics: list[str]
    explicit_epics = getattr(args, "epic", None)
    if explicit_epics:
        selected_epics = sorted({item.strip() for item in str(explicit_epics).split(",") if item.strip()})
    else:
        from_answers = answers_payload.get("flow_next", {}).get("epic_ids") if isinstance(answers_payload.get("flow_next"), dict) else None
        if isinstance(from_answers, list):
            selected_epics = sorted({str(item).strip() for item in from_answers if str(item).strip()})
        elif not bool(getattr(args, "non_interactive", False)) and epics:
            default_epics = ",".join(_default_active_epics(epics))
            prompted = _prompt("Flow-Next epics to import (comma separated)", default_epics)
            selected_epics = sorted({item.strip() for item in prompted.split(",") if item.strip()})
        else:
            selected_epics = _default_active_epics(epics)

    flow_summary["selected_epics"] = selected_epics
    flow_summary["epics"] = epics
    flow_summary["tasks"] = [task for task in tasks if task["epic_id"] in set(selected_epics)]

    if not flow_summary["tasks"] and selected_epics:
        warnings.append("No Flow-Next tasks matched the selected epic filters.")

    return flow_summary, warnings


def _flow_task_category(title: str) -> str:
    lowered = title.lower()
    if any(token in lowered for token in ["auth", "clerk", "jwt", "session", "organization", "oauth"]):
        return "auth"
    if any(token in lowered for token in ["migration", "schema", "rls", "policy", "supabase", "database", "seed"]):
        return "data"
    if any(token in lowered for token in ["graphql", "postgraphile", "query", "mutation", "subscription", "api", "realtime"]):
        return "api"
    if any(token in lowered for token in ["storage", "upload", "proof", "bucket", "file"]):
        return "storage"
    if any(token in lowered for token in ["offline", "sync", "indexeddb", "backoff", "network"]):
        return "offline"
    if any(token in lowered for token in ["audit", "rate", "cors", "csp", "sentry", "security"]):
        return "security"
    if any(token in lowered for token in ["deploy", "vercel", "checklist", "go-live", "phase", "ci", "github"]):
        return "delivery"
    return "other"


def _build_acceptance_criteria(
    *,
    answers: dict[str, Any],
    scan: dict[str, Any],
    flow_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    acs: list[dict[str, Any]] = []

    flow_tasks = flow_summary.get("tasks", []) if isinstance(flow_summary.get("tasks"), list) else []
    if flow_tasks:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for task in flow_tasks:
            grouped.setdefault(_flow_task_category(task["title"]), []).append(task)

        category_order = ["auth", "data", "api", "storage", "offline", "security", "delivery", "other"]
        summary_by_category = {
            "auth": "Users authenticate successfully and role-based access is enforced for protected workflows.",
            "data": "Core data is persisted with migrations and tenant-safe data access controls.",
            "api": "Application operations run through API/GraphQL interfaces with real-time updates where defined.",
            "storage": "File and proof assets are stored with tenant-safe validation and access controls.",
            "offline": "Offline actions synchronize reliably after connectivity is restored.",
            "security": "Runtime security and observability controls are active and verifiable.",
            "delivery": "Deployment and release gates verify production readiness end-to-end.",
            "other": "Imported implementation tasks produce observable user/system outcomes.",
        }

        for category in category_order:
            items = sorted(grouped.get(category, []), key=lambda row: row["id"])
            if not items:
                continue
            ac_id = f"AC-{len(acs) + 1}"
            acs.append(
                {
                    "id": ac_id,
                    "summary": summary_by_category[category],
                    "category": category,
                    "flow_task_ids": [row["id"] for row in items],
                    "flow_epic_ids": sorted({row["epic_id"] for row in items}),
                    "repo_evidence": [
                        row["path"]
                        for row in scan.get("detected_data_layer", [])[:3]
                        if isinstance(row, dict) and row.get("path")
                    ],
                    "answer_keys": ["operations", "definition_of_done"],
                }
            )

    if not acs:
        operations = answers["operations"]
        for op in operations:
            ac_id = f"AC-{len(acs) + 1}"
            verb = op["name"][0].lower() + op["name"][1:] if op["name"] else "complete operation"
            acs.append(
                {
                    "id": ac_id,
                    "summary": f"{op['actor']} can {verb} with validation, authorization, and observable result handling.",
                    "category": "operation",
                    "flow_task_ids": [],
                    "flow_epic_ids": [],
                    "repo_evidence": scan.get("detected_entrypoints", [])[:3],
                    "answer_keys": ["operations", "roles", "auth_requirement"],
                }
            )
            if len(acs) >= 8:
                break

    return acs


def _layer_for_ac(ac: dict[str, Any]) -> tuple[str, str]:
    summary = ac.get("summary", "").lower()
    if any(token in summary for token in ["migration", "schema", "rls", "policy", "database"]):
        if "policy" in summary or "rls" in summary:
            return "db", "policy"
        return "db", "migration"
    if any(token in summary for token in ["ui", "screen", "wizard", "flow", "offline"]):
        return "ui", "e2e"
    return "api", "integration"


def _build_test_plan(acs: list[dict[str, Any]], auth_requirement: str, has_data_layer: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    for ac in acs:
        layer, test_type = _layer_for_ac(ac)
        rows.append(
            {
                "test_id": f"T-{len(rows) + 1:03d}",
                "acceptance_criteria": ac["id"],
                "layer": layer,
                "type": test_type,
                "description": f"{ac['summary']} is validated for expected success behavior.",
            }
        )

    if auth_requirement != "none" and acs:
        target_ac = acs[0]["id"]
        rows.append(
            {
                "test_id": f"T-{len(rows) + 1:03d}",
                "acceptance_criteria": target_ac,
                "layer": "db" if has_data_layer else "api",
                "type": "policy" if has_data_layer else "integration",
                "description": "Unauthorized access is rejected while authorized role access succeeds.",
            }
        )

    return rows


def _map_operations_to_acs(operations: list[dict[str, Any]], acs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for idx, operation in enumerate(operations):
        candidate_ids: list[str] = []
        op_text = operation["name"].lower()
        for ac in acs:
            if any(token in ac["summary"].lower() for token in op_text.split()[:3]):
                candidate_ids.append(ac["id"])
        if not candidate_ids and acs:
            candidate_ids = [acs[min(idx, len(acs) - 1)]["id"]]
        mapped.append({**operation, "related_acs": sorted(set(candidate_ids))})
    return mapped


def _build_trace_map(
    *,
    scan: dict[str, Any],
    flow_summary: dict[str, Any],
    acs: list[dict[str, Any]],
    tests: list[dict[str, str]],
) -> dict[str, Any]:
    flow_epics = flow_summary.get("epics", []) if isinstance(flow_summary.get("epics"), list) else []
    flow_tasks = flow_summary.get("tasks", []) if isinstance(flow_summary.get("tasks"), list) else []

    ac_trace: dict[str, Any] = {}
    for ac in acs:
        ac_trace[ac["id"]] = {
            "summary": ac["summary"],
            "flow_next_ids": sorted(set(ac.get("flow_task_ids", []))),
            "epic_ids": sorted(set(ac.get("flow_epic_ids", []))),
            "repo_evidence": sorted(set(ac.get("repo_evidence", []))),
            "answer_keys": sorted(set(ac.get("answer_keys", []))),
            "mapped_tests": sorted(
                row["test_id"]
                for row in tests
                if row["acceptance_criteria"] == ac["id"]
            ),
        }

    task_to_acs: dict[str, list[str]] = {}
    for ac in acs:
        for task_id in ac.get("flow_task_ids", []):
            task_to_acs.setdefault(task_id, []).append(ac["id"])

    tasks_payload: dict[str, Any] = {}
    for task in flow_tasks:
        tasks_payload[task["id"]] = {
            "title": task["title"],
            "status": task["status"],
            "mapped_acceptance_criteria": sorted(task_to_acs.get(task["id"], [])),
        }

    epics_payload: dict[str, Any] = {}
    for epic in flow_epics:
        epics_payload[epic["id"]] = {
            "title": epic["title"],
            "status": epic["status"],
            "tasks": sorted(
                [task["id"] for task in flow_tasks if task["epic_id"] == epic["id"]],
                key=lambda item: int(item.split(".")[-1]),
            ),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "flow_next": {
            "enabled": bool(flow_summary.get("enabled", False)),
            "available": bool(flow_summary.get("available", False)),
            "selected_epics": sorted(flow_summary.get("selected_epics", [])),
            "validation": flow_summary.get("validation", {}),
        },
        "repo_scan": {
            "confidence": scan.get("confidence", {}),
            "detected_stack": scan.get("detected_stack", []),
            "detected_data_layer": scan.get("detected_data_layer", []),
        },
        "epics": epics_payload,
        "tasks": tasks_payload,
        "acceptance_criteria": ac_trace,
    }


def _render_spec_markdown(
    *,
    app_name: str,
    scope: list[str],
    non_goals: list[str],
    acs: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    tests: list[dict[str, str]],
    flow_summary: dict[str, Any],
) -> str:
    lines: list[str] = [f"# {app_name} — Wizard Generated Spec", "", "## Scope"]
    for item in scope:
        lines.append(f"- {item}")

    lines.extend(["", "## Non-goals"])
    for item in non_goals:
        lines.append(f"- {item}")

    lines.extend(["", "## Acceptance Criteria"])
    for ac in acs:
        if ac.get("flow_task_ids"):
            trace = ", ".join(sorted(set(ac["flow_epic_ids"] + ac["flow_task_ids"])))
            lines.append(f"- {ac['id']}: {ac['summary']} (Flow-Next: {trace})")
        else:
            evidence = ", ".join(ac.get("repo_evidence", [])[:3]) or "repo scan"
            answer_keys = ", ".join(ac.get("answer_keys", [])[:3]) or "wizard answers"
            lines.append(f"- {ac['id']}: {ac['summary']} (Trace: evidence={evidence}; answers={answer_keys})")

    lines.extend(["", "## Key Entities / Data Model Notes"])
    for entity in entities:
        fields = ", ".join(entity.get("fields", [])) or "TBD"
        lines.append(f"- Entity: {entity['name']}")
        lines.append(f"  - Fields: {fields}")
        lines.append("  - Relationships: Defined by operation ownership and access boundaries.")
        lines.append("  - Notes: High-level model only; exact schema is implementation-specific.")

    lines.extend(["", "## Endpoints / Operations", "(Describe in product terms; do not write full OpenAPI.)"])
    for operation in operations:
        lines.append(f"- Operation: {operation['name']}")
        lines.append(f"  - Actor: {operation['actor']}")
        lines.append(f"  - Inputs: {operation['inputs']}")
        lines.append(f"  - Output: {operation['output']}")
        lines.append(f"  - Error cases: {operation['error_cases']}")
        lines.append(f"  - Notes: {operation['notes'] or 'None.'}")
        lines.append(f"  - Related ACs: {', '.join(operation.get('related_acs', [])) or 'AC-1'}")

    lines.extend(["", "## TEST_PLAN", _TEST_PLAN_COLUMNS, "| --- | --- | --- | --- | --- |"])
    for row in tests:
        lines.append(
            f"| {row['test_id']} | {row['acceptance_criteria']} | {row['layer']} | {row['type']} | {row['description']} |"
        )

    if flow_summary.get("tasks"):
        included_epics = sorted(flow_summary.get("selected_epics", []))
        included_tasks = sorted([task["id"] for task in flow_summary.get("tasks", [])], key=lambda item: (item.split(".")[0], int(item.split(".")[-1])))
        lines.extend(
            [
                "",
                "## Flow-Next Traceability",
                f"- Epics included: {', '.join(included_epics) if included_epics else '(none)'}",
                f"- Tasks included: {', '.join(included_tasks) if included_tasks else '(none)'}",
            ]
        )

    lines.append("")
    return "\n".join(lines)


def _validate_generated_spec(
    *,
    spec_markdown: str,
    acs: list[dict[str, Any]],
    tests: list[dict[str, str]],
    trace_map: dict[str, Any],
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for heading in _REQUIRED_HEADINGS:
        if heading not in spec_markdown:
            errors.append(f"Missing required heading: {heading}")

    if _TEST_PLAN_COLUMNS not in spec_markdown:
        errors.append("Missing TEST_PLAN table with required columns.")

    if not acs:
        errors.append("No Acceptance Criteria generated.")

    test_by_ac: dict[str, int] = {ac["id"]: 0 for ac in acs}
    for row in tests:
        layer = row.get("layer", "")
        row_type = row.get("type", "")
        ac_id = row.get("acceptance_criteria", "")
        if layer not in _ALLOWED_LAYERS:
            errors.append(f"Invalid TEST_PLAN layer `{layer}` in {row.get('test_id', 'unknown')}")
        if row_type not in _ALLOWED_TEST_TYPES:
            errors.append(f"Invalid TEST_PLAN type `{row_type}` in {row.get('test_id', 'unknown')}")
        if ac_id in test_by_ac:
            test_by_ac[ac_id] += 1

    for ac_id, count in sorted(test_by_ac.items()):
        if count < 1:
            errors.append(f"{ac_id} has no TEST_PLAN mapping.")

    trace_tasks = trace_map.get("tasks", {})
    if isinstance(trace_tasks, dict):
        for task_id, payload in sorted(trace_tasks.items()):
            if not payload.get("mapped_acceptance_criteria"):
                errors.append(f"Imported Flow-Next task {task_id} is not mapped to any acceptance criterion.")

    return sorted(set(errors)), sorted(set(warnings))


def _warn_on_unclear_dependencies(
    *,
    scan: dict[str, Any],
    answers: dict[str, Any],
    skipped_questions: list[str],
) -> list[str]:
    warnings: list[str] = []

    confidence = scan.get("confidence", {})
    level = confidence.get("level")
    if level == "low" and skipped_questions:
        warnings.append("Repo scan confidence is low and some clarifying wizard questions were skipped.")

    operations_text = " ".join(op["name"].lower() for op in answers.get("operations", []))
    has_data_layer = bool(scan.get("detected_data_layer"))

    crud_keywords = ["create", "update", "delete", "sync", "write", "save"]
    if any(token in operations_text for token in crud_keywords) and not has_data_layer:
        warnings.append("Operations imply persistence but repository scan did not detect a clear data layer.")

    auth_keywords = ["admin", "permission", "role", "login", "auth", "user"]
    auth_requirement = answers.get("auth_requirement", "none")
    if any(token in operations_text for token in auth_keywords) and auth_requirement == "none":
        warnings.append("Operations imply auth/permissions but auth requirement is set to `none`.")

    return sorted(set(warnings))


def _provenance_for_repo(repo_root: Path) -> dict[str, Any]:
    commit_result = run_command(["git", "rev-parse", "HEAD"], cwd=repo_root, timeout_sec=10)
    commit = commit_result.stdout.strip() if commit_result.exit_code == 0 and commit_result.stdout.strip() else "unknown"

    node_result = run_command(["node", "--version"], cwd=repo_root, timeout_sec=10)
    node_version = node_result.stdout.strip() if node_result.exit_code == 0 else None

    return {
        "target_repo": str(repo_root),
        "target_repo_commit": commit,
        "target_node_version": node_version,
    }


def _build_scope(answers: dict[str, Any], scan: dict[str, Any]) -> list[str]:
    stack_names = [item.get("name") for item in scan.get("detected_stack", []) if isinstance(item, dict)]
    stack_blurb = ", ".join(stack_names[:5]) if stack_names else "repository-detected stack"
    scope = [
        f"Generate a swarm-skills-compatible specification for {answers['app_name']} based on deterministic repository evidence ({stack_blurb}).",
        f"Cover primary roles ({', '.join(answers['roles'])}) and declared auth mode (`{answers['auth_requirement']}`).",
        f"Define testable acceptance outcomes and mapped TEST_PLAN rows for {len(answers['operations'])} core operations.",
        f"Definition of done: {answers['definition_of_done']}",
    ]
    return scope


def _run_followup_commands(args: Any, workspace_root: Path, spec_path: Path) -> tuple[list[dict[str, Any]], list[str], bool]:
    followup_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    any_failure = False

    if bool(getattr(args, "run_contracts", False)):
        contracts_args = Namespace(
            workspace_root=str(workspace_root),
            spec=str(spec_path),
            test_plan_source=None,
            json=False,
        )
        exit_code = plan_to_contracts.run(contracts_args)
        followup_results.append({"command": "plan_to_contracts", "exit_code": exit_code})
        if exit_code != 0:
            any_failure = True

    if bool(getattr(args, "run_pipeline", False)):
        pipeline_args = Namespace(
            workspace_root=str(workspace_root),
            spec=str(spec_path),
            template=None,
            network=False,
            strict=False,
            stop_on_fail=True,
            steps=None,
            triage_on_fail=True,
            json=False,
        )
        exit_code = pipeline.run(pipeline_args)
        followup_results.append({"command": "pipeline", "exit_code": exit_code})
        if exit_code != 0:
            any_failure = True

    if any_failure:
        warnings.append("At least one follow-up command failed. Check downstream GateReports.")

    return followup_results, warnings, any_failure


def run(args: Any) -> int:
    workspace_root = Path(getattr(args, "workspace_root", ".")).resolve()
    skill_run = SkillRun(skill="spec_wizard", workspace_root=workspace_root, artifact_dir_name="spec_wizard")

    repo_arg = getattr(args, "repo", None)
    if not repo_arg:
        skill_run.add_note("Missing required --repo path.")
        return skill_run.finalize(
            "fail",
            emit_json=getattr(args, "json", False),
            summary_updates={"schema_version": SCHEMA_VERSION, "warnings_count": 0, "overall_status": "fail"},
        )

    repo_root = Path(str(repo_arg)).expanduser().resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        skill_run.add_note(f"Target repo path does not exist: {repo_root}")
        return skill_run.finalize(
            "fail",
            emit_json=getattr(args, "json", False),
            summary_updates={"schema_version": SCHEMA_VERSION, "warnings_count": 0, "overall_status": "fail"},
        )

    if bool(getattr(args, "non_interactive", False)) and not getattr(args, "answers", None):
        skill_run.add_note("--non-interactive requires --answers <answers.json>.")
        return skill_run.finalize(
            "fail",
            emit_json=getattr(args, "json", False),
            summary_updates={"schema_version": SCHEMA_VERSION, "warnings_count": 0, "overall_status": "fail"},
        )

    app_name_hint = _infer_app_name(repo_root)

    scan_payload = scan_repo(repo_root)
    repo_scan_path = skill_run.run_dir / "repo_scan.json"
    write_json(repo_scan_path, scan_payload)
    skill_run.record_artifact(repo_scan_path)

    answers_payload_for_flow: dict[str, Any] = {}
    if getattr(args, "answers", None):
        answers_path = Path(args.answers).expanduser().resolve()
        if answers_path.exists() and answers_path.is_file():
            try:
                answers_payload_for_flow = json.loads(answers_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                answers_payload_for_flow = {}

    flow_summary, flow_warnings = _flow_import(repo_root=repo_root, args=args, answers_payload=answers_payload_for_flow)

    answers, skipped_questions = _collect_answers(app_name_hint=app_name_hint, scan=scan_payload, args=args)

    if getattr(args, "app_name", None):
        answers["app_name"] = str(args.app_name).strip() or answers["app_name"]

    acs = _build_acceptance_criteria(answers=answers, scan=scan_payload, flow_summary=flow_summary)
    operations = _map_operations_to_acs(answers["operations"], acs)

    has_data_layer = bool(scan_payload.get("detected_data_layer"))
    tests = _build_test_plan(acs, answers["auth_requirement"], has_data_layer=has_data_layer)

    scope = _build_scope(answers, scan_payload)

    trace_map_payload = _build_trace_map(
        scan=scan_payload,
        flow_summary=flow_summary,
        acs=acs,
        tests=tests,
    )

    spec_markdown = _render_spec_markdown(
        app_name=answers["app_name"],
        scope=scope,
        non_goals=answers["non_goals"],
        acs=acs,
        entities=answers["entities"],
        operations=operations,
        tests=tests,
        flow_summary=flow_summary,
    )

    spec_errors, _ = _validate_generated_spec(
        spec_markdown=spec_markdown,
        acs=acs,
        tests=tests,
        trace_map=trace_map_payload,
    )
    warnings = _warn_on_unclear_dependencies(scan=scan_payload, answers=answers, skipped_questions=skipped_questions)
    warnings.extend(flow_warnings)

    default_spec_name = f"{_sanitize_slug(answers['app_name'])}_wizard.md"
    default_spec_path = workspace_root / "examples" / "specs" / default_spec_name
    out_arg = getattr(args, "out", None)
    if out_arg:
        out_path = Path(out_arg).expanduser()
        spec_output_path = out_path if out_path.is_absolute() else (workspace_root / out_path)
    else:
        spec_output_path = default_spec_path
    spec_output_path = spec_output_path.resolve()
    spec_output_path.parent.mkdir(parents=True, exist_ok=True)
    spec_output_path.write_text(spec_markdown, encoding="utf-8")

    generated_spec_copy = skill_run.run_dir / "generated_spec.md"
    generated_spec_copy.write_text(spec_markdown, encoding="utf-8")

    spec_json_payload = {
        "schema_version": SCHEMA_VERSION,
        "app_name": answers["app_name"],
        "target_repo": str(repo_root),
        "spec_path": str(spec_output_path),
        "scope": scope,
        "non_goals": answers["non_goals"],
        "acceptance_criteria": [
            {
                "id": ac["id"],
                "summary": ac["summary"],
                "flow_next_ids": sorted(set(ac.get("flow_task_ids", []))),
                "repo_evidence": sorted(set(ac.get("repo_evidence", []))),
                "answer_keys": sorted(set(ac.get("answer_keys", []))),
            }
            for ac in acs
        ],
        "entities": answers["entities"],
        "operations": operations,
        "test_plan": tests,
        "flow_next": {
            "enabled": bool(flow_summary.get("enabled", False)),
            "selected_epics": flow_summary.get("selected_epics", []),
        },
        "validation": {
            "errors": spec_errors,
            "warnings": sorted(set(warnings)),
            "warnings_count": len(sorted(set(warnings))),
        },
    }

    spec_json_path = skill_run.run_dir / "spec.json"
    trace_map_path = skill_run.run_dir / "trace_map.json"
    write_json(spec_json_path, spec_json_payload)
    write_json(trace_map_path, trace_map_payload)

    skill_run.record_artifact(spec_json_path)
    skill_run.record_artifact(trace_map_path)
    skill_run.record_artifact(generated_spec_copy)

    followup_results, followup_warnings, followup_failed = _run_followup_commands(args, workspace_root, spec_output_path)
    warnings.extend(followup_warnings)

    gate_status = "PASS"
    if spec_errors or followup_failed:
        gate_status = "FAIL"
    elif warnings:
        gate_status = "WARN"

    gate_lines = [
        "# Spec Wizard GateReport",
        "",
        f"Status: {gate_status}",
        "",
        "## Summary",
        f"- Target repo: `{repo_root}`",
        f"- App name: `{answers['app_name']}`",
        f"- Output spec: `{spec_output_path}`",
        f"- Acceptance criteria: {len(acs)}",
        f"- TEST_PLAN rows: {len(tests)}",
        f"- Flow-Next enabled: `{bool(flow_summary.get('enabled', False))}`",
        "",
        "## Warnings",
    ]

    if warnings:
        for warning in sorted(set(warnings)):
            gate_lines.append(f"- {warning}")
    else:
        gate_lines.append("- None")

    gate_lines.extend(["", "## Errors"])
    if spec_errors:
        for error in spec_errors:
            gate_lines.append(f"- {error}")
    elif followup_failed:
        gate_lines.append("- Follow-up command failure detected.")
    else:
        gate_lines.append("- None")

    gate_lines.extend(["", "## Follow-up Commands"])
    if followup_results:
        for row in followup_results:
            gate_lines.append(f"- `{row['command']}` exit_code={row['exit_code']}")
    else:
        gate_lines.append("- None")

    gate_lines.extend(
        [
            "",
            "## Runbook Command",
            "```bash",
            "python -m skills spec_wizard "
            + " ".join(
                [
                    f"--repo {shlex.quote(str(repo_root))}",
                    f"--out {shlex.quote(str(spec_output_path))}",
                    "--flow-next" if bool(getattr(args, "flow_next", False)) else "",
                    f"--epic {shlex.quote(str(getattr(args, 'epic', '')))}" if getattr(args, "epic", None) else "",
                    "--non-interactive" if bool(getattr(args, "non_interactive", False)) else "",
                    f"--answers {shlex.quote(str(getattr(args, 'answers', '')))}" if getattr(args, "answers", None) else "",
                    "--run-contracts" if bool(getattr(args, "run_contracts", False)) else "",
                    "--run-pipeline" if bool(getattr(args, "run_pipeline", False)) else "",
                ]
            ).strip(),
            "```",
        ]
    )

    gate_report_path = skill_run.run_dir / "GateReport.md"
    gate_report_path.write_text("\n".join(gate_lines) + "\n", encoding="utf-8")
    skill_run.record_artifact(gate_report_path)

    if spec_output_path.is_relative_to(workspace_root):
        skill_run.record_artifact(spec_output_path)

    overall_status = "fail" if gate_status == "FAIL" else ("warn" if gate_status == "WARN" else "pass")
    summary_status = "fail" if gate_status == "FAIL" else "pass"

    if gate_status == "FAIL":
        skill_run.add_note("Spec wizard gate failed. See artifacts/spec_wizard/latest/GateReport.md")
    elif gate_status == "WARN":
        skill_run.add_note("Spec wizard completed with warnings. See artifacts/spec_wizard/latest/GateReport.md")
    else:
        skill_run.add_note("Spec wizard completed successfully.")

    summary_updates = {
        "schema_version": SCHEMA_VERSION,
        "overall_status": overall_status,
        "warnings_count": len(sorted(set(warnings))),
        "generated_spec_path": str(spec_output_path),
        "repo_scan_path": str(repo_scan_path),
        "spec_json_path": str(spec_json_path),
        "trace_map_path": str(trace_map_path),
        "gate_report_path": str(gate_report_path),
        "followup_results": followup_results,
    }

    provenance = _provenance_for_repo(repo_root)
    return skill_run.finalize(
        summary_status,
        emit_json=bool(getattr(args, "json", False)),
        provenance=provenance,
        summary_updates=summary_updates,
    )
