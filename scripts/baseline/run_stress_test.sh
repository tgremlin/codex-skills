#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

spec_path="examples/specs/crud_rbac_audit_job.md"
started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
ts="$(date -u +"%Y%m%dT%H%M%SZ")"
run_dir="artifacts/baseline/$ts"
latest_dir="artifacts/baseline/latest"
mkdir -p "$run_dir"

python -m skills pipeline --spec "$spec_path" --triage-on-fail --orchestrator > "$run_dir/pipeline_result.json" 2> "$run_dir/pipeline.stderr.log"

ended_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
repo_commit="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
triage_root_cause="artifacts/triage/latest/root_cause.md"
triage_patch_plan="artifacts/triage/latest/patch_plan.md"
triage_summary="artifacts/triage/latest/summary.json"

if [[ ! -f "$triage_root_cause" ]]; then
  triage_root_cause=""
fi
if [[ ! -f "$triage_patch_plan" ]]; then
  triage_patch_plan=""
fi
if [[ ! -f "$triage_summary" ]]; then
  triage_summary=""
fi

cat > "$run_dir/stress_test_summary.json" <<JSON
{
  "repo_commit": "$repo_commit",
  "started_at": "$started_at",
  "ended_at": "$ended_at",
  "spec": "$spec_path",
  "pipeline_result_path": "$run_dir/pipeline_result.json",
  "canonical_pipeline_result": "artifacts/pipeline/latest/pipeline_result.json",
  "triage": {
    "root_cause_path": "$triage_root_cause",
    "patch_plan_path": "$triage_patch_plan",
    "summary_path": "$triage_summary"
  }
}
JSON

rm -rf "$latest_dir"
cp -R "$run_dir" "$latest_dir"

echo "Stress test complete: $run_dir"
echo "Summary: $run_dir/stress_test_summary.json"
