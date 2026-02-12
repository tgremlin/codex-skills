# Product Spec: CRUD + RBAC + Audit + Scheduled Job

## Goal

Deliver a multi-faceted todo system with role-based access control, auditable actions, and a scheduled retention task.

## Scope

- Users authenticate and operate under one of three roles: `admin`, `member`, `viewer`.
- Todo CRUD remains available with role constraints.
- Audit records capture who performed key actions and when.
- A scheduled job performs audit retention cleanup and records last-run status.
- Role assignments, audit records, and job status are persisted.

## Acceptance Criteria

- AC-1: Admin can create, update, and delete any todo; member can CRUD only own todos; viewer is read-only.
- AC-2: Unauthorized or forbidden requests return consistent auth/permission errors.
- AC-3: Create, update, delete, role-change, and retention-job actions produce persisted audit log entries with actor, action, target, and timestamp.
- AC-4: Scheduled retention job runs on schedule (or manual trigger), updates persisted last-run status, and purges audit rows older than configured retention days.
- AC-5: UI exposes role-aware controls and an audit/job-status view that renders persisted backend data.

## Data Persistence Requirements

- Persist user role assignments (`users.role`).
- Persist audit events (`audit_log`).
- Persist scheduled job metadata (`job_status.last_run_at`, `job_status.last_result`, retention configuration).

## TEST_PLAN Mapping

| test_id | acceptance_ids | layers | description |
|---|---|---|---|
| TC-AC1-API-RBAC | AC-001 | api,db | Verify role-based CRUD policy for admin/member/viewer and persisted ownership checks. |
| TC-AC1-UI-RBAC | AC-001 | ui | Verify UI shows/hides create/edit/delete actions by role. |
| TC-AC2-AUTH | AC-002 | api | Verify 401/403 behavior for unauthenticated and forbidden requests. |
| TC-AC3-AUDIT-API | AC-003 | api,db | Verify audit rows are written with actor/action/target/timestamp for key actions. |
| TC-AC3-AUDIT-UI | AC-003 | ui | Verify audit view renders persisted audit records. |
| TC-AC4-JOB | AC-004 | api,db | Verify retention job updates last-run status and purges old audit rows. |
| TC-AC4-JOB-UI | AC-004 | ui | Verify job status view shows last-run/result data from backend. |
| TC-AC5-END2END | AC-005 | ui,api,db | Verify role-aware UI flows execute against real endpoints and persisted data. |
