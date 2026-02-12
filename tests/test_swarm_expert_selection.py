from __future__ import annotations

from pathlib import Path

from swarm_skills.swarm.selection import select_experts_deterministic


def _touch(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_selection_always_includes_security_and_testing(tmp_path: Path) -> None:
    experts = select_experts_deterministic(tmp_path, "build feature", max_experts=6)
    assert experts[0] == "SecurityExpert"
    assert experts[1] == "TestingExpert"


def test_selection_adds_specialists_from_repo_scan(tmp_path: Path) -> None:
    _touch(tmp_path / "app" / "page.tsx")
    _touch(tmp_path / "server" / "main.py")
    _touch(tmp_path / "prisma" / "schema.prisma")
    _touch(tmp_path / "Dockerfile")

    experts = select_experts_deterministic(tmp_path, "implement dashboard", max_experts=6)
    assert "BackendExpert" in experts
    assert "FrontendExpert" in experts
    assert "DBExpert" in experts
    assert "DevOpsExpert" in experts


def test_selection_respects_max_experts_but_keeps_required(tmp_path: Path) -> None:
    _touch(tmp_path / "app" / "page.tsx")
    experts = select_experts_deterministic(tmp_path, "docs update", max_experts=2)
    assert experts == ["SecurityExpert", "TestingExpert"]
