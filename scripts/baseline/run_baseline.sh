#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

python_bin="python3"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  python_bin="python"
fi

started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
ts="$(date -u +"%Y%m%dT%H%M%SZ")"
run_dir="artifacts/baseline/$ts"
latest_dir="artifacts/baseline/latest"
mkdir -p "$run_dir"

set +e
"$python_bin" -m skills template_check --all --json > "$run_dir/template_check.json" 2> "$run_dir/template_check.stderr.log"
template_check_exit=$?
"$python_bin" -m skills bench --spec-dir examples/specs --append-history --json > "$run_dir/bench.json" 2> "$run_dir/bench.stderr.log"
bench_exit=$?
"$python_bin" -m skills matrix --spec-dir examples/specs --templates all --json > "$run_dir/matrix.json" 2> "$run_dir/matrix.stderr.log"
matrix_exit=$?
set -e

ended_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
repo_commit="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
overall_status="pass"
if [[ $template_check_exit -ne 0 || $bench_exit -ne 0 || $matrix_exit -ne 0 ]]; then
  overall_status="fail"
fi

cat > "$run_dir/baseline_summary.json" <<JSON
{
  "repo_commit": "$repo_commit",
  "started_at": "$started_at",
  "ended_at": "$ended_at",
  "overall_status": "$overall_status",
  "exit_codes": {
    "template_check": $template_check_exit,
    "bench": $bench_exit,
    "matrix": $matrix_exit
  },
  "template_check": {
    "stdout_json": "$run_dir/template_check.json",
    "latest_summary": "artifacts/template_check/latest/summary.json",
    "latest_report": "artifacts/template_check/latest/report.json"
  },
  "bench": {
    "stdout_json": "$run_dir/bench.json",
    "latest_summary": "artifacts/bench/latest/summary.json",
    "latest_results": "artifacts/bench/latest/bench_results.json"
  },
  "matrix": {
    "stdout_json": "$run_dir/matrix.json",
    "latest_summary": "artifacts/matrix/latest/summary.json",
    "latest_results": "artifacts/matrix/latest/matrix.json"
  }
}
JSON

rm -rf "$latest_dir"
cp -R "$run_dir" "$latest_dir"

echo "Baseline run complete: $run_dir"
echo "Summary: $run_dir/baseline_summary.json"

if [[ "$overall_status" != "pass" ]]; then
  exit 1
fi
