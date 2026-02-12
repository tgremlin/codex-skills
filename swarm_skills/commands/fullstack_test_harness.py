from __future__ import annotations

import json
import os
import re
import shlex
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from swarm_skills.catalog import resolve_template
from swarm_skills.commands.plan_to_contracts import parse_test_plan_markdown
from swarm_skills.runtime import SkillRun, copy_or_replace_dir, run_command, write_json


def _default_test_plan_path(workspace_root: Path) -> Path:
    return workspace_root / "artifacts" / "contracts" / "latest" / "TEST_PLAN.md"


def _default_template_id(workspace_root: Path) -> str:
    choice_path = workspace_root / "artifacts" / "plan" / "template_choice.json"
    if choice_path.exists():
        raw = json.loads(choice_path.read_text(encoding="utf-8"))
        selected = raw.get("selected_template", {})
        template_id = selected.get("id")
        if template_id:
            return str(template_id)
    return "local-node-http-crud"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_json(url: str, method: str = "GET", payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url=url, method=method, data=data, headers=headers)
    with urlopen(request, timeout=5) as response:  # nosec B310
        body_text = response.read().decode("utf-8")
        body = json.loads(body_text) if body_text else {}
        return int(response.status), body


def _wait_for_health(url: str, timeout_sec: int) -> tuple[bool, str]:
    deadline = time.time() + timeout_sec
    last_error = ""
    while time.time() < deadline:
        try:
            status, payload = _http_json(url)
            if 200 <= status < 300:
                return True, json.dumps(payload, sort_keys=True)
            last_error = f"HTTP {status}: {payload}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(0.5)
    return False, last_error


def _extract_test_cmd(template: Any) -> list[str] | None:
    for item in template.boot.get("health_strategy", []):
        if isinstance(item, str) and item.startswith("test_cmd:"):
            cmd_text = item.split(":", 1)[1].strip()
            if cmd_text:
                return shlex.split(cmd_text)
    return None


def _read_todos_data(template_path: Path) -> tuple[bool, str, int]:
    data_path = template_path / "data" / "todos.json"
    if not data_path.exists():
        return False, f"Missing data file: {data_path}", 0
    try:
        raw = json.loads(data_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, f"Invalid JSON in {data_path}: {exc}", 0
    if not isinstance(raw, list):
        return False, f"Expected list in {data_path}", 0
    return True, "ok", len(raw)


def _no_network_mode(template: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    ui_results = {
        "mode": "no-network",
        "status": "skipped",
        "tests": [
            {
                "id": "UI-NO-NET-001",
                "status": "skipped",
                "reason": "No UI runner configured in no-network mode.",
            }
        ],
    }

    api_test_cmd = _extract_test_cmd(template)
    api_tests: list[dict[str, Any]] = []
    if api_test_cmd is None:
        api_tests.append(
            {
                "id": "API-NO-NET-001",
                "status": "failed",
                "error": "Template does not declare health_strategy test_cmd for no-network mode.",
            }
        )
        api_status = "fail"
    else:
        result = run_command(api_test_cmd, cwd=template.path, timeout_sec=90)
        api_tests.append(
            {
                "id": "API-NO-NET-001",
                "status": "passed" if result.exit_code == 0 else "failed",
                "cmd": result.cmd,
                "exit_code": result.exit_code,
                "stdout": result.stdout[-1000:],
                "stderr": result.stderr[-1000:],
            }
        )
        api_status = "pass" if result.exit_code == 0 else "fail"

    api_results = {
        "mode": "no-network",
        "status": api_status,
        "tests": api_tests,
    }

    db_ok, db_message, row_count = _read_todos_data(template.path)
    db_results = {
        "mode": "no-network",
        "status": "pass" if db_ok else "fail",
        "tests": [
            {
                "id": "DB-NO-NET-001",
                "status": "passed" if db_ok else "failed",
                "message": db_message,
                "row_count": row_count,
            }
        ],
    }

    return ui_results, api_results, db_results


def _network_mode(template: Any, health_timeout_sec: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    ui_results = {
        "mode": "network",
        "status": "skipped",
        "tests": [
            {
                "id": "UI-NET-001",
                "status": "skipped",
                "reason": "No browser runner configured; API+DB exercised through network CRUD checks.",
            }
        ],
    }

    boot_cmd = template.boot.get("command")
    if not isinstance(boot_cmd, list) or not boot_cmd:
        api_results = {
            "mode": "network",
            "status": "fail",
            "tests": [
                {
                    "id": "API-NET-BOOT-001",
                    "status": "failed",
                    "error": "Template missing boot.command",
                }
            ],
        }
        db_results = {
            "mode": "network",
            "status": "skipped",
            "tests": [{"id": "DB-NET-001", "status": "skipped", "reason": "boot failed"}],
        }
        return ui_results, api_results, db_results

    try:
        port = _find_free_port()
    except OSError as exc:
        api_results = {
            "mode": "network",
            "status": "fail",
            "tests": [
                {
                    "id": "API-NET-BOOT-001",
                    "status": "failed",
                    "error": f"Unable to allocate ephemeral port: {exc}",
                }
            ],
        }
        db_results = {
            "mode": "network",
            "status": "skipped",
            "tests": [
                {
                    "id": "DB-NET-001",
                    "status": "skipped",
                    "reason": "Ephemeral port allocation failed.",
                }
            ],
        }
        return ui_results, api_results, db_results
    health_path = str(template.boot.get("health_path", "/api/health"))
    health_url = f"http://127.0.0.1:{port}{health_path}"
    todos_url = f"http://127.0.0.1:{port}/api/todos"

    env = os.environ.copy()
    env["PORT"] = str(port)
    process = subprocess.Popen(
        boot_cmd,
        cwd=str(template.path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    api_tests: list[dict[str, Any]] = []
    db_results: dict[str, Any]
    try:
        healthy, detail = _wait_for_health(health_url, timeout_sec=health_timeout_sec)
        api_tests.append(
            {
                "id": "API-NET-HEALTH-001",
                "status": "passed" if healthy else "failed",
                "url": health_url,
                "detail": detail,
            }
        )

        if healthy:
            status, list_payload = _http_json(todos_url, "GET")
            api_tests.append(
                {
                    "id": "API-NET-LIST-001",
                    "status": "passed" if status == 200 and isinstance(list_payload, list) else "failed",
                    "status_code": status,
                }
            )

            status, created = _http_json(todos_url, "POST", {"title": "from-harness"})
            created_id = created.get("id") if isinstance(created, dict) else None
            api_tests.append(
                {
                    "id": "API-NET-CREATE-001",
                    "status": "passed" if status == 201 and created_id is not None else "failed",
                    "status_code": status,
                }
            )

            if created_id is not None:
                status, updated = _http_json(
                    f"{todos_url}/{created_id}",
                    "PUT",
                    {"title": "updated", "completed": True},
                )
                api_tests.append(
                    {
                        "id": "API-NET-UPDATE-001",
                        "status": "passed"
                        if status == 200 and isinstance(updated, dict) and updated.get("completed") is True
                        else "failed",
                        "status_code": status,
                    }
                )

                status, deleted = _http_json(f"{todos_url}/{created_id}", "DELETE")
                api_tests.append(
                    {
                        "id": "API-NET-DELETE-001",
                        "status": "passed" if status == 200 and isinstance(deleted, dict) else "failed",
                        "status_code": status,
                    }
                )

        db_ok, db_message, row_count = _read_todos_data(template.path)
        db_results = {
            "mode": "network",
            "status": "pass" if db_ok else "fail",
            "tests": [
                {
                    "id": "DB-NET-001",
                    "status": "passed" if db_ok else "failed",
                    "message": db_message,
                    "row_count": row_count,
                    "port": port,
                }
            ],
        }
    except (URLError, OSError, ValueError, TimeoutError) as exc:
        api_tests.append(
            {
                "id": "API-NET-UNEXPECTED-001",
                "status": "failed",
                "error": str(exc),
            }
        )
        db_results = {
            "mode": "network",
            "status": "skipped",
            "tests": [
                {
                    "id": "DB-NET-001",
                    "status": "skipped",
                    "reason": "Network checks failed before DB assertions.",
                    "port": port,
                }
            ],
        }
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        stdout, stderr = process.communicate(timeout=1)
        api_tests.append(
            {
                "id": "API-NET-PROCESS-001",
                "status": "passed" if process.returncode in (0, -15) else "failed",
                "return_code": process.returncode,
                "stdout": stdout[-1200:],
                "stderr": stderr[-1200:],
                "port": port,
            }
        )

    api_status = "pass" if all(row["status"] == "passed" for row in api_tests) else "fail"
    api_results = {
        "mode": "network",
        "status": api_status,
        "tests": api_tests,
    }

    return ui_results, api_results, db_results


def _gate_and_report(
    tests_dir: Path,
    ui_results: dict[str, Any],
    api_results: dict[str, Any],
    db_results: dict[str, Any],
    test_plan_path: Path,
) -> tuple[bool, str]:
    failing: list[dict[str, str]] = []
    for layer_name, payload in [("ui", ui_results), ("api", api_results), ("db", db_results)]:
        for test in payload.get("tests", []):
            if test.get("status") == "failed":
                excerpt = str(test.get("error") or test.get("stderr") or test.get("detail") or test.get("message") or "")
                failing.append(
                    {
                        "id": str(test.get("id", "unknown")),
                        "layer": layer_name,
                        "excerpt": excerpt[:220],
                    }
                )

    gate_ok = len(failing) == 0
    lines = ["# Full-Stack GateReport", ""]
    if gate_ok:
        lines.extend(
            [
                "Status: PASS",
                "",
                "All non-skipped layer checks passed.",
                "",
                "Next fix steps:",
                "1. Keep this gate green while implementing backend_build/frontend_bind against the same contracts.",
            ]
        )
    else:
        lines.extend(["Status: FAIL", "", "Failing test IDs:"])
        for row in failing:
            lines.append(f"- {row['id']} ({row['layer']}): {row['excerpt']}")
        lines.extend(
            [
                "",
                "Next fix steps:",
                f"1. Review {test_plan_path} for expected flows and layer coverage.",
                "2. Fix template runtime behavior in `templates/local-node-http-crud/server.js` or no-network checks in `templates/local-node-http-crud/scripts/no_network_check.js`.",
                "3. Re-run `python -m skills fullstack_test_harness` (add `--network` locally when port binding is available).",
            ]
        )

    report_path = tests_dir / "GateReport.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return gate_ok, str(report_path)


def run(args: Any) -> int:
    workspace_root = Path(args.workspace_root).resolve()
    skill_run = SkillRun(skill="fullstack_test_harness", workspace_root=workspace_root)

    template_ref = args.template or _default_template_id(workspace_root)
    test_plan_path = (workspace_root / args.test_plan).resolve() if args.test_plan else _default_test_plan_path(workspace_root)

    if not test_plan_path.exists():
        skill_run.add_note(f"TEST_PLAN not found: {test_plan_path}")
        return skill_run.finalize("fail", emit_json=args.json)

    test_rows = parse_test_plan_markdown(test_plan_path.read_text(encoding="utf-8"))
    if not test_rows:
        skill_run.add_note("TEST_PLAN has no parseable test rows.")
        return skill_run.finalize("fail", emit_json=args.json)

    try:
        template = resolve_template(template_ref, workspace_root)
    except FileNotFoundError as exc:
        skill_run.add_note(str(exc))
        return skill_run.finalize("fail", emit_json=args.json)

    tests_dir = workspace_root / "artifacts" / "tests" / skill_run.timestamp
    tests_latest = workspace_root / "artifacts" / "tests" / "latest"
    tests_dir.mkdir(parents=True, exist_ok=True)

    if args.network:
        ui_results, api_results, db_results = _network_mode(template, args.health_timeout_sec)
        skill_run.add_note("Assumption: network mode requested; running ephemeral HTTP CRUD checks.")
    else:
        ui_results, api_results, db_results = _no_network_mode(template)
        skill_run.add_note("Assumption: no-network mode is default for CI/sandbox compatibility.")

    ui_path = tests_dir / "ui_results.json"
    api_path = tests_dir / "api_results.json"
    db_path = tests_dir / "db_results.json"

    write_json(ui_path, ui_results)
    write_json(api_path, api_results)
    write_json(db_path, db_results)

    gate_ok, report_path = _gate_and_report(tests_dir, ui_results, api_results, db_results, test_plan_path)

    tests_summary = {
        "mode": "network" if args.network else "no-network",
        "template": {
            "id": template.id,
            "path": str(template.path.relative_to(workspace_root)),
            "version": template.version,
        },
        "test_plan_path": str(test_plan_path.relative_to(workspace_root)),
        "status": "pass" if gate_ok else "fail",
        "counts": {
            "test_plan_rows": len(test_rows),
            "ui_tests": len(ui_results.get("tests", [])),
            "api_tests": len(api_results.get("tests", [])),
            "db_tests": len(db_results.get("tests", [])),
        },
    }
    tests_summary_path = tests_dir / "summary.json"
    write_json(tests_summary_path, tests_summary)

    for path in [ui_path, api_path, db_path, Path(report_path), tests_summary_path]:
        skill_run.record_artifact(path)

    copy_or_replace_dir(tests_dir, tests_latest)

    if not gate_ok:
        skill_run.add_note("Harness gate failed. See artifacts/tests/latest/GateReport.md")
        return skill_run.finalize(
            "fail",
            emit_json=args.json,
            provenance={
                "template_id": template.id,
                "template_version": template.version,
            },
        )

    skill_run.add_note("Harness gate passed.")
    return skill_run.finalize(
        "pass",
        emit_json=args.json,
        provenance={
            "template_id": template.id,
            "template_version": template.version,
        },
    )
