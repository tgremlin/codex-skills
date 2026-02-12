# Operator Guide

This guide is for maintainers and operators running the swarm skills pack locally or in automation.

## 1) Prerequisites

- Python 3.11+ (3.12 verified)
- Node.js 18+ (Node 20 used in CI)
- Local venv at `/mnt/Storage/Repos/codex-skills/.venv` for test runs

Quick environment check:

```bash
cd /mnt/Storage/Repos/codex-skills
python3 -m skills doctor
```

## 2) Safe Working Mode

If your primary repo workspace gets noisy from parallel sessions/tools, run validations from a dedicated worktree.

Create/use release worktree:

```bash
git -C /mnt/Storage/Repos/codex-skills worktree add -f /tmp/codex-skills-verify release/swarm-skills-v1
cd /tmp/codex-skills-verify
```

Fast wrapper (if installed):

```bash
/home/allan/run_swarm_skills.sh all
```

## 3) Core Validation Commands

From repo root (or safe worktree root):

```bash
PYTHONPATH=. /mnt/Storage/Repos/codex-skills/.venv/bin/pytest -q

python3 skills.py pipeline \
  --workspace-root "$(pwd)" \
  --spec examples/SPEC.todo.md \
  --orchestrator > /tmp/pipeline.manual.json
```

Inspect key fields:

```bash
python3 - <<'PY'
import json
obj=json.load(open('/tmp/pipeline.manual.json'))
print('overall_status:', obj.get('overall_status'))
print('handoff_contract_path:', obj.get('handoff_contract_path'))
print('handoff_contract_sha256_present:', bool(obj.get('handoff_contract_sha256')))
PY
```

## 4) Baseline and Stress Runs

Baseline regression sweep:

```bash
./scripts/baseline/run_baseline.sh || true
cat artifacts/baseline/latest/baseline_summary.json
```

Stress spec with auto-triage:

```bash
./scripts/baseline/run_stress_test.sh || true
cat artifacts/baseline/latest/stress_test_summary.json
```

If stress fails, inspect triage outputs:

```bash
ls -la artifacts/triage/latest
sed -n '1,160p' artifacts/triage/latest/root_cause.md
sed -n '1,160p' artifacts/triage/latest/patch_plan.md
```

## 5) Branch and Release Flow

Recommended branch roles:

- `release/swarm-skills-v1`: clean release line
- `chore/workspace-cleanup`: unrelated cleanup churn

Current release checklist:

- `docs/push_and_ci_checklist.md`

Typical networked-shell publish steps:

```bash
cd /mnt/Storage/Repos/codex-skills

git switch release/swarm-skills-v1
git push origin release/swarm-skills-v1
git push origin v1.0.0-rc1

# after CI is green:
git tag -a v1.0.0 -m "Swarm skills pack v1" f689dc7
git push origin v1.0.0
```

## 6) Troubleshooting Workspace Drift

### Symptom

Tracked files under `skills/` appear deleted/modified unexpectedly while you are not editing them.

### Quick Recovery

```bash
cd /mnt/Storage/Repos/codex-skills
git restore --source=HEAD --worktree --staged .
```

### Verify critical integration files exist

```bash
git ls-files --error-unmatch \
  skills/handoff_contract.json \
  skills/swarm_integration_recipe.json \
  skills/policy_rules_v1.md \
  skills/policy_rules_v1.json
```

### If drift recurs repeatedly

- Use isolated worktree (`/tmp/codex-skills-verify`) for all validation commands.
- Close stale parallel Codex sessions before long runs.
- Avoid running unrelated cleanup tooling in the same working tree.

## 7) Reference Docs

- Architecture/workflow: `scripts/skills/WORKFLOW.md`
- Contract rules: `scripts/skills/CONTRACTS.md`
- Compatibility policy: `docs/compat.md`
- Push and CI checklist: `docs/push_and_ci_checklist.md`
