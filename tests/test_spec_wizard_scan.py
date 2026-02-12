from __future__ import annotations

from pathlib import Path

from swarm_skills.commands import spec_wizard


FIXTURES = Path(__file__).parent / "fixtures" / "spec_wizard"


def test_scan_repo_is_deterministic_next_supabase_fixture() -> None:
    repo = (FIXTURES / "next_supabase_app").resolve()

    first = spec_wizard.scan_repo(repo)
    second = spec_wizard.scan_repo(repo)

    assert first == second
    assert first["schema_version"] == "1.0"

    stack_names = [row["name"] for row in first["detected_stack"]]
    assert "nextjs" in stack_names
    assert "supabase" in stack_names

    data_layer_types = [row["type"] for row in first["detected_data_layer"]]
    assert "migrations" in data_layer_types
    assert first["confidence"]["evidence_count"] > 0


def test_scan_repo_detects_python_stack_fixture() -> None:
    repo = (FIXTURES / "python_fastapi_app").resolve()

    payload = spec_wizard.scan_repo(repo)

    stack_names = [row["name"] for row in payload["detected_stack"]]
    assert "fastapi" in stack_names

    packages = payload["detected_packages"]
    assert "fastapi" in packages
    assert "sqlalchemy" in packages
