from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RegistrySkill:
    id: str
    cli: str
    required_inputs: tuple[str, ...]
    produced_artifacts: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class RegistryData:
    entrypoint: str
    pack: str
    version: str
    skills: tuple[RegistrySkill, ...]


def _registry_path() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "scripts" / "skills" / "registry.json"


def load_registry() -> RegistryData:
    path = _registry_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    skills = tuple(
        RegistrySkill(
            id=item["id"],
            cli=item["cli"],
            required_inputs=tuple(item.get("required_inputs", [])),
            produced_artifacts=tuple(item.get("produced_artifacts", [])),
            status=item.get("status", "unknown"),
        )
        for item in raw.get("skills", [])
    )
    return RegistryData(
        entrypoint=raw.get("entrypoint", "python -m skills"),
        pack=raw.get("pack", "unknown"),
        version=raw.get("version", "0.0.0"),
        skills=skills,
    )


def registry_as_json(registry: RegistryData) -> dict[str, Any]:
    return {
        "entrypoint": registry.entrypoint,
        "pack": registry.pack,
        "skills": [
            {
                "cli": skill.cli,
                "id": skill.id,
                "produced_artifacts": list(skill.produced_artifacts),
                "required_inputs": list(skill.required_inputs),
                "status": skill.status,
            }
            for skill in registry.skills
        ],
        "version": registry.version,
    }
