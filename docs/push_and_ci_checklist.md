# Push And CI Checklist

Use this checklist from a networked shell.

Branch intent:

- `release/swarm-skills-v1`: clean branch for v1 release/tag.
- `chore/workspace-cleanup`: optional branch for unrelated cleanup churn.

## Push Release Branch + Tag

1. `cd /mnt/Storage/Repos/codex-skills`
2. `git switch release/swarm-skills-v1`
3. `git push origin release/swarm-skills-v1`
4. `git tag -a v1.0.0 -m "Swarm skills pack v1"`
5. `git push --tags`

Optional cleanup branch push:

1. `git switch chore/workspace-cleanup`
2. `git push origin chore/workspace-cleanup`

## Verify Merge Gate On GitHub

1. Open the pushed `release/swarm-skills-v1` commit/PR in GitHub.
2. Open **Actions** and verify workflow `Merge Gate` completed successfully.
3. Confirm these commands ran in CI:
   - `python -m skills doctor`
   - `python -m skills pipeline --spec examples/SPEC.todo.md`

## Verify Failure Artifact Uploads (Branch Test)

1. Create a temporary branch.
2. Intentionally break one golden shape test (example: remove a required key in `tests/golden/pipeline_result.shape.json`).
3. Push the branch and open a PR.
4. Confirm `Merge Gate` fails.
5. In workflow run artifacts, download `swarm-skills-gate-artifacts`.
6. Verify uploaded paths include `artifacts/pipeline/latest/*` and per-skill `artifacts/**/latest/*`.
7. Revert the intentional break before merge.
