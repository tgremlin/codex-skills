from __future__ import annotations

from swarm_skills.swarm.models import ExpertDefinition


GLOBAL_SAFETY_RULES = [
    "Never exfiltrate secrets.",
    "Never print or log environment variable values.",
    "Never modify CI secrets or secret-bearing workflow files.",
]

GLOBAL_DENY_PATTERNS = (
    ".github/workflows/**",
    "**/.env",
    "**/.env.*",
    "**/secrets/**",
    "**/*secret*",
)

EXPERT_DEFINITIONS: dict[str, ExpertDefinition] = {
    "SecurityExpert": ExpertDefinition(
        name="SecurityExpert",
        role_prompt=(
            "You are the security specialist. Identify and fix vulnerabilities, enforce safe defaults, "
            "and ensure no secrets handling regressions."
        ),
        allowed_paths=(
            "swarm_skills/**",
            "skills/**",
            "templates/**",
            "docs/**",
            "scripts/**",
            "tests/**",
            "*.md",
        ),
    ),
    "TestingExpert": ExpertDefinition(
        name="TestingExpert",
        role_prompt=(
            "You are the testing specialist. Improve or add deterministic tests and reliability checks "
            "for the requested changes."
        ),
        allowed_paths=(
            "tests/**",
            "swarm_skills/**",
            "scripts/**",
            "docs/**",
            "templates/**",
        ),
    ),
    "BackendExpert": ExpertDefinition(
        name="BackendExpert",
        role_prompt=(
            "You are the backend specialist. Implement API, business logic, and service-layer changes "
            "with deterministic behavior."
        ),
        allowed_paths=(
            "swarm_skills/**",
            "skills/**",
            "templates/**",
            "app/**",
            "api/**",
            "server/**",
            "backend/**",
            "scripts/**",
        ),
    ),
    "FrontendExpert": ExpertDefinition(
        name="FrontendExpert",
        role_prompt=(
            "You are the frontend specialist. Implement UI behavior, state, rendering, and client integration "
            "changes."
        ),
        allowed_paths=(
            "templates/**",
            "frontend/**",
            "app/**",
            "pages/**",
            "components/**",
            "src/**",
            "public/**",
            "styles/**",
        ),
    ),
    "DBExpert": ExpertDefinition(
        name="DBExpert",
        role_prompt=(
            "You are the database specialist. Implement schema, migration, and data-layer changes without "
            "touching unrelated frontend files."
        ),
        allowed_paths=(
            "**/migrations/**",
            "**/models/**",
            "**/schema.prisma",
            "**/*.sql",
            "**/alembic/**",
            "**/db/**",
            "artifacts/contracts/**",
        ),
    ),
    "DevOpsExpert": ExpertDefinition(
        name="DevOpsExpert",
        role_prompt=(
            "You are the DevOps specialist. Improve deterministic build/test execution, scripts, and infra configs "
            "without touching secrets."
        ),
        allowed_paths=(
            "Dockerfile",
            "Dockerfile.*",
            "docker/**",
            "k8s/**",
            "helm/**",
            "scripts/**",
            "Makefile",
            "*.yml",
            "*.yaml",
        ),
    ),
    "DocsExpert": ExpertDefinition(
        name="DocsExpert",
        role_prompt=(
            "You are the documentation specialist. Keep architecture and operator docs aligned with behavior and "
            "flags."
        ),
        allowed_paths=(
            "docs/**",
            "README.md",
            "CHANGELOG.md",
            "examples/specs/**",
            "scripts/**",
            "*.md",
        ),
    ),
}


def required_output_schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ["summary", "changed_files", "risks"],
        "properties": {
            "summary": {"type": "string"},
            "changed_files": {"type": "array", "items": {"type": "string"}},
            "tests_run": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
        },
    }
