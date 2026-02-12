# Codex Swarm Team Mode

`codex-swarm` is a Codex-only multi-agent runner that adds a team-execution layer on top of the existing deterministic `python -m skills pipeline` gates.

## Architecture

Execution loop:

1. Plan
2. Expert selection
3. Parallel expert execution in isolated git worktrees
4. Deterministic integration on an integration branch/worktree
5. Gate oracle (`python -m skills pipeline`)
6. Retry routing (bounded by budgets)

Security guarantees:

- `SecurityExpert` always runs a first pass and a final pass per iteration.
- `TestingExpert` is always included.
- Hard safety policy in prompts and path policy:
  - never exfiltrate secrets
  - never log env var values
  - never modify CI secrets/workflow secret files
- Expert path allowlists are enforced post-run; out-of-scope changes are reverted before patch collection.

## Commands

```bash
codex-swarm plan --repo . --goal "<text>"
codex-swarm run --repo . --goal "<text>" --autofix --max-iterations 3
codex-swarm gen-spec --repo . --goal "<text>"
```

Run-mode flags:

- `--autofix`: enable multi-iteration retry loop (otherwise one iteration)
- `--dry-run`: simulate expert outputs without invoking Codex
- `--max-iterations`: retry bound
- `--max-experts`: expert count bound
- `--time-budget`: wall-clock budget in seconds
- `--max-diff-lines`: merged patch size bound
- `--planner-augmentation`: optional Codex planner augmentation for expert selection

## Artifact Layout

Each run writes:

- `artifacts/swarm_run/<timestamp>/plan.json`
- `artifacts/swarm_run/<timestamp>/assignments.json`
- `artifacts/swarm_run/<timestamp>/spec_resolution.json`
- `artifacts/swarm_run/<timestamp>/transcripts/*`
- `artifacts/swarm_run/<timestamp>/patches/*`
- `artifacts/swarm_run/<timestamp>/gate_reports/*`
- `artifacts/swarm_run/<timestamp>/summary.json`
- `artifacts/swarm_run/latest/*` (copy of latest run)

## Spec Resolution (Deterministic)

If `--spec` is provided:

- absolute override mode (`provided`)
- discovery is skipped

If `--spec` is omitted, discovery order is:

1. `artifacts/flow_next_spec/latest/*.md`
2. `examples/specs/*.md`
3. `docs/specs/*.md`
4. repo root: `*spec*.md`, `*requirements*.md`

When multiple candidates exist:

- choose the most recently modified file (`mtime`)
- tie-break by path lexicographically
- record full candidate list and chosen file

If no spec is found:

- default: fail fast with exit code `2`
- optional recovery: `--gen-spec-if-missing` generates `artifacts/flow_next_spec/<timestamp>/generated_spec.md`, updates `artifacts/flow_next_spec/latest/`, then continues

No-spec message:

```text
No spec found.
Options:
  1) codex-swarm gen-spec --repo . --goal '<goal>'
  2) codex-swarm run --repo . --goal '<goal>' --gen-spec-if-missing
  3) Provide --spec /path/to/spec.md
```

## Exit Codes

- `0`: success
- `1`: run failed (integration/gates/budget)
- `2`: missing spec without `--gen-spec-if-missing`
