from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

HEADER = (
    "Output ONLY a unified diff. No commentary. "
    "Start with diff --git (preferred) or ---/+++. "
    "Do not edit tests. Do not edit dependencies. "
    "Keep changes within constraints.\n\n"
)


def _build_cmd(repo_dir: str, model_id: str, meta: dict[str, Any], include_ask: bool = True) -> list[str]:
    cmd = ["codex", "exec", "--sandbox", "read-only"]
    if include_ask:
        cmd.extend(["--ask-for-approval", "never"])
    cmd.extend(["--cd", repo_dir])
    use_json = bool(meta.get("use_json", False))
    if use_json:
        cmd.append(meta.get("json_flag", "--json"))
    use_model_flag = meta.get("use_model_flag", True)
    model_flag = meta.get("model_flag", "--model")
    if model_id and use_model_flag and model_flag:
        cmd.extend([model_flag, model_id])
    return cmd


def _extract_from_json(stdout: str) -> str:
    chunks: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if isinstance(event, dict):
            msg = event.get("message")
            if isinstance(msg, dict):
                if msg.get("role") == "assistant":
                    content = msg.get("content")
                    if isinstance(content, str):
                        chunks.append(content)
                    elif isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict):
                                text = item.get("text")
                                if text:
                                    chunks.append(text)
            text = event.get("text") or event.get("content")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)


def generate(prompt: str, model_id: str, meta: dict[str, Any]):
    repo_dir = meta.get("repo_dir")
    if not repo_dir:
        raise RuntimeError("repo_dir missing from provider meta")
    repo_dir = str(Path(repo_dir))
    timeout_s = int(meta.get("timeout_s", 180))
    use_json = bool(meta.get("use_json", False))

    cmd = _build_cmd(repo_dir, model_id, meta, include_ask=True)
    prompt_text = HEADER + prompt

    def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            input=prompt_text,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )

    result = _run(cmd)

    if result.returncode != 0 and "unexpected argument '--ask-for-approval'" in (result.stderr or ""):
        cmd = _build_cmd(repo_dir, model_id, meta, include_ask=False)
        result = _run(cmd)

    if result.returncode != 0:
        stderr_tail = (result.stderr or "")[-2000:]
        raise RuntimeError(
            f"codex exec failed (exit {result.returncode}): {stderr_tail}\ncmd={' '.join(cmd)}"
        )

    stdout = result.stdout or ""
    if use_json:
        extracted = _extract_from_json(stdout)
        if extracted:
            return extracted.rstrip() + "\n", {
                "repo_dir": repo_dir,
                "model_id": model_id,
                "timeout_s": timeout_s,
                "use_json": use_json,
                "flags_used": cmd,
            }
    return stdout.rstrip() + "\n", {
        "repo_dir": repo_dir,
        "model_id": model_id,
        "timeout_s": timeout_s,
        "use_json": use_json,
        "flags_used": cmd,
    }
