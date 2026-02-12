from __future__ import annotations

import asyncio
import fnmatch
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from swarm_skills.runtime import write_json
from swarm_skills.swarm.models import ExpertAssignment, ExpertResult, SwarmArtifacts
from swarm_skills.swarm.policy import GLOBAL_DENY_PATTERNS


def _run_sync(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _count_diff_lines(patch_text: str) -> int:
    lines = 0
    for raw in patch_text.splitlines():
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+") or raw.startswith("-"):
            lines += 1
    return lines


def _extract_json_blob(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if not raw:
        return None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _matches_any(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    for pattern in patterns:
        if fnmatch.fnmatch(normalized, pattern):
            return True
    return False


def _allowed_file(path: str, allowed_patterns: list[str]) -> bool:
    if _matches_any(path, list(GLOBAL_DENY_PATTERNS)):
        return False
    return _matches_any(path, allowed_patterns)


def _git_changed_files(worktree_dir: Path) -> list[str]:
    result = _run_sync(["git", "diff", "--name-only"], cwd=worktree_dir)
    if result.returncode != 0:
        return []
    files = [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]
    return sorted(set(files))


def _enforce_allowlist(worktree_dir: Path, allowed_patterns: list[str]) -> tuple[list[str], list[str]]:
    changed = _git_changed_files(worktree_dir)
    blocked: list[str] = []
    for rel in changed:
        if _allowed_file(rel, allowed_patterns):
            continue
        blocked.append(rel)
        _run_sync(["git", "checkout", "--", rel], cwd=worktree_dir)

    final_changed = _git_changed_files(worktree_dir)
    return final_changed, sorted(set(blocked))


async def _run_codex(
    *,
    codex_bin: str,
    worktree_dir: Path,
    prompt: str,
    timeout_sec: int,
) -> tuple[int, str, str, list[str]]:
    base_cmd = [codex_bin, "exec", "--ask-for-approval", "never", "--cd", str(worktree_dir)]
    attempted: list[list[str]] = []

    async def _invoke(cmd: list[str]) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout=timeout_sec)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, "", "timeout"
        return proc.returncode, stdout_b.decode("utf-8", errors="ignore"), stderr_b.decode("utf-8", errors="ignore")

    attempted.append(base_cmd)
    code, out, err = await _invoke(base_cmd)
    if code != 0 and "unexpected argument '--ask-for-approval'" in err:
        fallback = [codex_bin, "exec", "--cd", str(worktree_dir)]
        attempted.append(fallback)
        code, out, err = await _invoke(fallback)

    return code, out, err, [" ".join(item) for item in attempted]


def _prepare_worktree(repo_root: Path, worktree_dir: Path, base_ref: str) -> tuple[bool, str]:
    if worktree_dir.exists():
        _run_sync(["git", "worktree", "remove", "--force", str(worktree_dir)], cwd=repo_root)

    result = _run_sync(["git", "worktree", "add", "--detach", str(worktree_dir), base_ref], cwd=repo_root)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git worktree add failed").strip()
        return False, detail
    return True, "ok"


def _cleanup_worktree(repo_root: Path, worktree_dir: Path) -> None:
    _run_sync(["git", "worktree", "remove", "--force", str(worktree_dir)], cwd=repo_root)


def _git_patch(worktree_dir: Path) -> str:
    result = _run_sync(["git", "diff", "--binary"], cwd=worktree_dir)
    if result.returncode != 0:
        return ""
    return result.stdout


def _build_prompt(assignment: ExpertAssignment, spec_text: str) -> str:
    allowed = "\n".join(f"- {item}" for item in assignment.allowed_paths)
    schema_json = json.dumps(assignment.required_output_schema, indent=2, sort_keys=True)
    return (
        f"Role: {assignment.expert}\n"
        f"Role guidance: {assignment.role_prompt}\n\n"
        f"Task:\n{assignment.task}\n\n"
        "Hard safety rules:\n"
        "- Never exfiltrate secrets.\n"
        "- Never print environment variable values.\n"
        "- Never modify CI secret files or workflow secrets.\n\n"
        "Allowed path globs (do not edit files outside this set):\n"
        f"{allowed}\n\n"
        "Required output schema (respond in JSON, no markdown):\n"
        f"{schema_json}\n\n"
        "SPEC:\n"
        f"{spec_text}\n"
    )


async def _execute_one(
    *,
    assignment: ExpertAssignment,
    repo_root: Path,
    base_ref: str,
    artifacts: SwarmArtifacts,
    codex_bin: str,
    timeout_sec: int,
    batch_id: str,
    spec_text: str,
    dry_run: bool,
) -> ExpertResult:
    expert_slug = assignment.expert.lower()
    worktree_dir = artifacts.run_dir / "worktrees" / f"{batch_id}-{expert_slug}"
    patch_path = artifacts.patches_dir / f"{batch_id}_{expert_slug}.patch"
    patch_meta_path = artifacts.patches_dir / f"{batch_id}_{expert_slug}.json"
    transcript_path = artifacts.transcripts_dir / f"{batch_id}_{expert_slug}.json"
    prompt_path = artifacts.transcripts_dir / f"{batch_id}_{expert_slug}.prompt.md"

    prompt = _build_prompt(assignment, spec_text)
    prompt_path.write_text(prompt, encoding="utf-8")

    if dry_run:
        patch_path.write_text("", encoding="utf-8")
        write_json(
            patch_meta_path,
            {
                "expert": assignment.expert,
                "status": "simulated",
                "changed_files": [],
                "diff_line_count": 0,
                "patch_path": str(patch_path),
            },
        )
        payload = {
            "expert": assignment.expert,
            "status": "simulated",
            "summary": "dry-run simulated output; no file edits applied",
            "stdout": "",
            "stderr": "",
            "commands": [],
        }
        write_json(transcript_path, payload)
        return ExpertResult(
            expert=assignment.expert,
            status="pass",
            summary=payload["summary"],
            changed_files=[],
            patch_path=str(patch_path),
            transcript_path=str(transcript_path),
            diff_line_count=0,
            metadata={"dry_run": True},
        )

    ok, detail = _prepare_worktree(repo_root, worktree_dir, base_ref)
    if not ok:
        write_json(
            transcript_path,
            {
                "expert": assignment.expert,
                "status": "worktree_error",
                "summary": detail,
            },
        )
        return ExpertResult(
            expert=assignment.expert,
            status="fail",
            summary=detail,
            changed_files=[],
            patch_path=None,
            transcript_path=str(transcript_path),
            diff_line_count=0,
        )

    try:
        code, stdout, stderr, commands = await _run_codex(
            codex_bin=codex_bin,
            worktree_dir=worktree_dir,
            prompt=prompt,
            timeout_sec=timeout_sec,
        )

        changed_files, blocked_files = _enforce_allowlist(worktree_dir, assignment.allowed_paths)
        patch_text = _git_patch(worktree_dir)
        patch_path.write_text(patch_text, encoding="utf-8")
        diff_lines = _count_diff_lines(patch_text)
        write_json(
            patch_meta_path,
            {
                "expert": assignment.expert,
                "status": "pass" if code == 0 else "fail",
                "changed_files": changed_files,
                "blocked_files": blocked_files,
                "diff_line_count": diff_lines,
                "patch_path": str(patch_path),
            },
        )

        payload = _extract_json_blob(stdout) or {}
        summary = str(payload.get("summary") or "").strip()
        if not summary:
            summary = (stdout or stderr or "no summary returned").strip().splitlines()[0:1]
            summary = summary[0] if summary else "no summary returned"

        transcript = {
            "expert": assignment.expert,
            "status": "pass" if code == 0 else "fail",
            "summary": summary,
            "stdout": stdout,
            "stderr": stderr,
            "commands": commands,
            "blocked_files": blocked_files,
        }
        write_json(transcript_path, transcript)

        return ExpertResult(
            expert=assignment.expert,
            status="pass" if code == 0 else "fail",
            summary=summary,
            changed_files=changed_files,
            patch_path=str(patch_path),
            transcript_path=str(transcript_path),
            diff_line_count=diff_lines,
            metadata={"blocked_files": blocked_files, "return_code": code},
        )
    finally:
        _cleanup_worktree(repo_root, worktree_dir)


async def execute_assignments(
    *,
    assignments: list[ExpertAssignment],
    repo_root: Path,
    base_ref: str,
    artifacts: SwarmArtifacts,
    codex_bin: str,
    timeout_sec: int,
    batch_id: str,
    spec_text: str,
    dry_run: bool,
) -> list[ExpertResult]:
    tasks = [
        _execute_one(
            assignment=assignment,
            repo_root=repo_root,
            base_ref=base_ref,
            artifacts=artifacts,
            codex_bin=codex_bin,
            timeout_sec=timeout_sec,
            batch_id=batch_id,
            spec_text=spec_text,
            dry_run=dry_run,
        )
        for assignment in assignments
    ]
    results = await asyncio.gather(*tasks)
    return sorted(results, key=lambda item: item.expert)
