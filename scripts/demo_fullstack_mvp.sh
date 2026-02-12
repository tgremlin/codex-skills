#!/usr/bin/env bash
set -u -o pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
DEMO_DIR="artifacts/demo/${TS}"
LATEST_DIR="artifacts/demo/latest"
mkdir -p "$DEMO_DIR"
STEPS_FILE="$DEMO_DIR/steps.ndjson"
EXIT_SUMMARY="$DEMO_DIR/exit_codes.json"
GATE_REPORT="$DEMO_DIR/GateReport.md"

run_step() {
  local step="$1"
  shift
  local cmd=("$@")

  set +e
  "${cmd[@]}"
  local code=$?
  set -e

  local joined
  joined=$(printf '%q ' "${cmd[@]}")
  printf '{"step":"%s","command":"%s","exit_code":%d}\n' "$step" "${joined% }" "$code" >> "$STEPS_FILE"
  return "$code"
}

set -e

FAILED=0
run_step "doctor" python3 -m skills doctor || FAILED=1
if [ "$FAILED" -eq 0 ]; then
  run_step "template_select" python3 -m skills template_select --spec examples/SPEC.todo.md || FAILED=1
fi
if [ "$FAILED" -eq 0 ]; then
  run_step "scaffold_verify" python3 -m skills scaffold_verify --template local-node-http-crud --port auto || FAILED=1
fi
if [ "$FAILED" -eq 0 ]; then
  run_step "plan_to_contracts" python3 -m skills plan_to_contracts --spec examples/SPEC.todo.md || FAILED=1
fi
if [ "$FAILED" -eq 0 ]; then
  run_step "fullstack_test_harness" python3 -m skills fullstack_test_harness --template local-node-http-crud || FAILED=1
fi

python3 - <<'PY' "$STEPS_FILE" "$EXIT_SUMMARY" "$GATE_REPORT"
import json
import sys
from pathlib import Path

steps_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
report_path = Path(sys.argv[3])

rows = [json.loads(line) for line in steps_path.read_text(encoding="utf-8").splitlines() if line.strip()]
status = "pass" if rows and all(row["exit_code"] == 0 for row in rows) else "fail"
first_failure = next((row for row in rows if row["exit_code"] != 0), None)

summary = {
    "status": status,
    "steps": rows,
}
summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

lines = ["# Demo GateReport", "", f"Status: {status.upper()}", "", "Executed commands:"]
for row in rows:
    lines.append(f"- {row['step']}: `{row['command']}` (exit={row['exit_code']})")

lines.extend([
    "",
    "Per-skill artifact pointers:",
    "- doctor: `artifacts/doctor/latest/summary.json`",
    "- template_select: `artifacts/template_select/latest/summary.json`",
    "- scaffold_verify: `artifacts/scaffold_verify/latest/summary.json`",
    "- contracts: `artifacts/contracts/latest/contracts_summary.json`",
    "- fullstack tests: `artifacts/tests/latest/summary.json`",
])

if first_failure:
    lines.extend([
        "",
        f"Stopped early at step `{first_failure['step']}`.",
        "",
        "Next fix steps:",
        "1. Open the step's latest summary and gate report artifacts.",
        "2. Apply minimal fix in template or command implementation.",
        "3. Re-run `./scripts/demo_fullstack_mvp.sh`.",
    ])

report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
PY

rm -rf "$LATEST_DIR"
cp -R "$DEMO_DIR" "$LATEST_DIR"

if [ "$FAILED" -ne 0 ]; then
  echo "Demo failed. See $GATE_REPORT"
  exit 1
fi

echo "Demo passed. Summary: $EXIT_SUMMARY"
