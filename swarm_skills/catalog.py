from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TemplateMetadata:
    id: str
    version: str
    name: str
    description: str
    status: str
    risk_flags: tuple[str, ...]
    capabilities: dict[str, bool]
    runbook: dict[str, Any]
    boot: dict[str, Any]
    path: Path

    @property
    def is_bootable(self) -> bool:
        cmd = self.boot.get("command")
        return self.status == "active" and isinstance(cmd, list) and len(cmd) > 0


def _load_template_metadata(path: Path) -> TemplateMetadata:
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw_capabilities = raw.get("capabilities", {})
    if isinstance(raw_capabilities, list):
        capabilities = {
            str(item): True
            for item in raw_capabilities
            if isinstance(item, str) and item.strip()
        }
    elif isinstance(raw_capabilities, dict):
        capabilities = {str(key): bool(value) for key, value in raw_capabilities.items()}
    else:
        capabilities = {}
    return TemplateMetadata(
        id=raw["id"],
        version=str(raw.get("version", "0.1.0")),
        name=raw["name"],
        description=raw["description"],
        status=raw["status"],
        risk_flags=tuple(raw.get("risk_flags", [])),
        capabilities=capabilities,
        runbook=dict(raw.get("runbook", {})),
        boot=dict(raw.get("boot", {})),
        path=path.parent,
    )


def load_templates(workspace_root: Path) -> list[TemplateMetadata]:
    templates_root = workspace_root / "templates"
    results: list[TemplateMetadata] = []
    for manifest in sorted(templates_root.glob("*/template.json")):
        results.append(_load_template_metadata(manifest))
    return sorted(results, key=lambda t: t.id)


def resolve_template(reference: str, workspace_root: Path) -> TemplateMetadata:
    templates = load_templates(workspace_root)
    id_map = {template.id: template for template in templates}
    if reference in id_map:
        return id_map[reference]

    template_path = (workspace_root / reference).resolve()
    manifest = template_path / "template.json"
    if manifest.exists():
        return _load_template_metadata(manifest)

    raise FileNotFoundError(
        f"Template '{reference}' not found. Use a template id from templates/*/template.json or a template path."
    )
