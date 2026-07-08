# Agent rules — deal-flow

Rules for Claude running via GitHub Actions on this repo. Read fully before acting.

## Project

Internal AI deal-flow tool for Raed Ventures. Assesses MENA deep-tech leads from
Copper, drafts outreach, tracks portfolio outcomes. Production:
https://deal-flow.apps.raed.vc (Raed apps platform — auth via platform proxy,
`X-Auth-Email` header is trusted; there is no login screen).

## Stack and conventions

- `backend/` — FastAPI + SQLAlchemy (async) + Celery + Alembic. Python 3.11.
  - API routes in `backend/app/routers/` under `/api/v1/*`.
  - Business logic in `backend/app/services/`; Celery jobs in `backend/app/tasks/`.
  - DB schema changes ALWAYS via an Alembic migration in `backend/alembic/` —
    migrations run on container boot. Never edit models without a migration.
  - Config via env vars only (`backend/app/config.py`). Never hardcode keys.
- `frontend/` — React 18 + Vite + TypeScript + TanStack Query + Tailwind + zustand.
  - Pages in `frontend/src/pages/`, shared UI in `frontend/src/components/`,
    API calls via the axios client in `frontend/src/api/`.
- Follow existing patterns in neighboring files before inventing new ones.

## Build loop rules (non-negotiable)

1. **Always run tests before opening the PR**: `cd backend && pytest tests/ -q`
   (set `DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/test`).
   Also verify the frontend builds if you touched it: `cd frontend && npm ci && npm run build`.
   Add tests for new behavior where the change is testable.
2. **Never touch `.github/workflows/`, secrets, `Dockerfile`, or
   `docker-compose.yml`** unless the issue explicitly asks for it.
3. **If blocked** (missing credential, access, or a decision only a human can
   make): comment the specific question on the issue, add the `blocked` label
   to the issue, and stop. Do NOT guess credentials, invent access, or build a
   mock in place of the real thing.
4. **One PR per issue**, on a fresh branch, PR body references the issue
   (`Closes #N`) and states how each acceptance criterion is met.
   **Open the pull request yourself** using your GitHub tools after pushing the
   branch — do NOT just post a "Create PR" link. The PR must be authored by
   claude[bot] so the reviewer workflow triggers.
5. **Never write `@claude` in your own comments** (it re-triggers the builder).
   The only exception: the reviewer workflow's single fix-instruction comment.
6. Respect the issue's **Out of scope** section literally.
7. No auto-merge, ever. Humans merge.

## Labels the loop uses

- `ready-for-qa` — reviewer applies when the PR passes review and CI is green.
- `blocked` — builder applies to an issue when it needs human input.
- `fix-round-1` / `fix-round-2` — reviewer's fix-pass counter. Hard cap: 2.
