# Product Spec: Team Todo Tracker

## Goal

Build a lightweight full-stack app where a team can add, update, and delete todos.

## Scope

- Users can create, list, update, and delete todos.
- UI shows live list after each mutation.
- Data must persist across server restarts.
- Keep auth out of scope for MVP.

## Acceptance Criteria

1. A user can create a todo from the homepage.
2. A user can mark a todo complete and see the new state immediately.
3. A user can delete a todo.
4. Data persists when the app restarts.
5. `GET /api/health` returns a successful health response.
