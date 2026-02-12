# Policy Rules V1 (Stub)

This is a deterministic orchestration policy stub for future runtime wiring.

## Intent

When a pipeline run fails, orchestrators should invoke triage and route targeted remediation based on classification.

## Rules

1. Run pipeline first.
2. If pipeline status is `fail`, run triage.
3. If triage classification indicates backend contract mismatch/runtime issue, run `backend_build`.
4. If triage classification indicates frontend binding issue, run `frontend_bind`.
5. Re-run pipeline after targeted remediation.
6. Stop when pipeline status is `pass`.

## Notes

- This document is config guidance only; no runtime enforcement is wired in this step.
- Classification source should be `artifacts/triage/latest/summary_payload.json` when available.
