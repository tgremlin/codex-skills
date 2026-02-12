from types import SimpleNamespace

from swarm_skills.commands import pipeline


def test_pipeline_writes_report_on_failure(tmp_path):
    spec = tmp_path / "SPEC.md"
    spec.write_text(
        """
# Spec

## Acceptance Criteria
- one
""".strip()
        + "\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        spec="SPEC.md",
        template=None,
        network=False,
        stop_on_fail=True,
        steps="template_select,scaffold_verify",
        json=False,
    )

    code = pipeline.run(args)
    assert code == 1

    latest = tmp_path / "artifacts" / "pipeline" / "latest"
    assert (latest / "summary.json").exists()
    assert (latest / "GateReport.md").exists()
    assert "Status: FAIL" in (latest / "GateReport.md").read_text(encoding="utf-8")
