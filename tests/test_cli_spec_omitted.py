from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from swarm_skills import cli


def _touch(path: Path, content: str = "# spec\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_cli_pipeline_omitted_spec_is_discovered(tmp_path, monkeypatch) -> None:
    workspace = tmp_path
    spec = _touch(workspace / "examples/specs/demo_wizard.md")

    captured: dict[str, str] = {}

    def fake_pipeline_run(args):
        captured["spec"] = args.spec
        return 0

    monkeypatch.setattr(cli, "pipeline", SimpleNamespace(run=fake_pipeline_run))

    code = cli.main(
        [
            "pipeline",
            "--workspace-root",
            str(workspace),
            "--steps",
            "frontend_bind",
        ]
    )

    assert code == 0
    assert captured["spec"] == str(spec.resolve())


def test_cli_orchestrator_returns_structured_error_on_discovery_failure(tmp_path, capsys) -> None:
    workspace = tmp_path

    code = cli.main(
        [
            "pipeline",
            "--workspace-root",
            str(workspace),
            "--orchestrator",
        ]
    )

    assert code == 1
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["schema_version"] == "1.0"
    assert payload["error_type"] == "spec_discovery_error"
    assert payload["status"] == "fail"
    assert "guidance" in payload
