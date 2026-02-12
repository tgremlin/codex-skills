# Product Spec: CRUD With Auth Baseline

## Goal

Provide todo CRUD flows with basic authenticated access assumptions.

## Scope

- Authenticated users can create, list, update, and delete their own todos.
- Unauthorized requests are rejected.
- Persisted data remains available after restart.

## Acceptance Criteria

1. Authenticated user can create a todo.
2. Authenticated user can edit and delete their todo.
3. Unauthenticated request to protected CRUD endpoint is rejected.
4. Data persists across app restarts.
