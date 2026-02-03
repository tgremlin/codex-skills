from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from teacher_providers import codex_cli_provider  # noqa: E402


class DummyResult(SimpleNamespace):
    pass


def test_args_and_stdout(monkeypatch):
    calls = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        calls["cmd"] = cmd
        calls["timeout"] = timeout
        return DummyResult(returncode=0, stdout="diff --git a/x b/x\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    meta = {"repo_dir": "/tmp/repo", "timeout_s": 123}
    out, meta_out = codex_cli_provider.generate("prompt", "model-1", meta)
    assert out.endswith("\n")
    assert meta_out["model_id"] == "model-1"
    assert calls["timeout"] == 123
    assert calls["cmd"][0:2] == ["codex", "exec"]
    assert "--sandbox" in calls["cmd"]
    assert "read-only" in calls["cmd"]
    assert "--ask-for-approval" in calls["cmd"]
    assert "never" in calls["cmd"]
    assert "--cd" in calls["cmd"]


def test_nonzero_exit(monkeypatch):
    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        return DummyResult(returncode=2, stdout="", stderr="boom" * 1000)

    monkeypatch.setattr(subprocess, "run", fake_run)
    meta = {"repo_dir": "/tmp/repo"}
    with pytest.raises(RuntimeError) as exc:
        codex_cli_provider.generate("prompt", "", meta)
    assert "codex exec failed" in str(exc.value)


def test_json_mode(monkeypatch):
    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        stdout = "{" + "\"message\":{\"role\":\"assistant\",\"content\":\"diff --git a/x b/x\\n\"}}"
        return DummyResult(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    meta = {"repo_dir": "/tmp/repo", "use_json": True}
    out, meta_out = codex_cli_provider.generate("prompt", "", meta)
    assert out.startswith("diff --git")


def test_ask_flag_fallback(monkeypatch):
    calls = []

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        calls.append(cmd)
        if len(calls) == 1:
            return DummyResult(
                returncode=2,
                stdout="",
                stderr="error: unexpected argument '--ask-for-approval' found",
            )
        return DummyResult(returncode=0, stdout="diff --git a/x b/x\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    meta = {"repo_dir": "/tmp/repo"}
    out, meta_out = codex_cli_provider.generate("prompt", "model-1", meta)
    assert out.startswith("diff --git")
    assert any("--ask-for-approval" in call for call in calls[0])
