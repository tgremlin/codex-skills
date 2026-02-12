#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
ts="$(date -u +"%Y%m%dT%H%M%SZ")"
run_dir="artifacts/baseline/$ts"
latest_dir="artifacts/baseline/latest"
mkdir -p "$run_dir"

python -m skills template_check --all --json > "$run_dir/template_check.json" 2> "$run_dir/template_check.stderr.log"
python -m skills bench --spec-dir examples/specs --append-history --json > "$run_dir/bench.json" 2> "$run_dir/bench.stderr.log"
python -m skills matrix --spec-dir examples/specs --templates all --json > "$run_dir/matrix.json" 2> "$run_dir/matrix.stderr.log"

ended_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
repo_commit="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

cat > "$run_dir/baseline_summary.json" <<JSON
{
  "repo_commit": "$repo_commit",
  "started_at": "$started_at",
  "ended_at": "$ended_at",
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
