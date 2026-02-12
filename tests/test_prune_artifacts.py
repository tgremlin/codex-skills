from __future__ import annotations

import json
from types import SimpleNamespace

from swarm_skills.commands import prune_artifacts


def _mkdir(path):
    path.mkdir(parents=True, exist_ok=True)


def test_prune_artifacts_deletes_old_and_preserves_latest_and_history(tmp_path):
    old_run = tmp_path / "artifacts" / "pipeline" / "20000101T000000Z"
    new_run = tmp_path / "artifacts" / "pipeline" / "20990101T000000Z"
    latest = tmp_path / "artifacts" / "pipeline" / "latest"
    bench_dir = tmp_path / "artifacts" / "bench"

    _mkdir(old_run)
    _mkdir(new_run)
    _mkdir(latest)
    _mkdir(bench_dir)

    (old_run / "summary.json").write_text("{}\n", encoding="utf-8")
    (new_run / "summary.json").write_text("{}\n", encoding="utf-8")
    (latest / "summary.json").write_text("{}\n", encoding="utf-8")
    (bench_dir / "history.jsonl").write_text('{"ok":true}\n', encoding="utf-8")

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        keep_days=30,
        keep_latest=True,
        dry_run=False,
        skills=None,
        json=False,
    )
    code = prune_artifacts.run(args)
    assert code == 0

    assert not old_run.exists()
    assert new_run.exists()
    assert latest.exists()
    assert (bench_dir / "history.jsonl").exists()

    report = json.loads((tmp_path / "artifacts" / "prune" / "latest" / "prune_report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "1.0"
    assert "artifacts/pipeline/20000101T000000Z" in report["deleted"]


def test_prune_artifacts_dry_run_does_not_delete(tmp_path):
    old_run = tmp_path / "artifacts" / "tests" / "20000101T000000Z"
    _mkdir(old_run)
    (old_run / "summary.json").write_text("{}\n", encoding="utf-8")

    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        keep_days=1,
        keep_latest=True,
        dry_run=True,
        skills="tests",
        json=False,
    )
    code = prune_artifacts.run(args)
    assert code == 0
    assert old_run.exists()

    report = json.loads((tmp_path / "artifacts" / "prune" / "latest" / "prune_report.json").read_text(encoding="utf-8"))
    assert report["deleted"] == []
    assert "artifacts/tests/20000101T000000Z" in report["would_delete"]
