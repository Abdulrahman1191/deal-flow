# CLAUDE.md — rules for the automated build loop

This repo runs a Claude GitHub Actions build loop: a spec is filed as an issue,
`@claude` is tagged, and Claude opens a tested PR. A separate reviewer agent then
checks that PR. These rules govern both agents. Keep this file concise.

## Project
- **Backend** (`backend/`): FastAPI + SQLAlchemy async (asyncpg) + Alembic + Celery.
- **Frontend** (`frontend/`): React + Vite + TypeScript + Tailwind.
- The app **auto-deploys to the Raed platform on every push to `main`**. Therefore
  **never push to `main` directly** — every change lands via a PR that a human merges.
- Alembic revision IDs are hand-written and sequential; keep the chain intact.
- Python files using `X | None` annotations need `from __future__ import annotations`.

## Always
- Work on a fresh branch. Open **one** PR that references the originating issue
  (put `Closes #<n>` in the PR body).
- **Run tests/build before opening the PR and make them pass:**
  - backend: `cd backend && python -m pytest -q`
  - frontend: `cd frontend && npm run build`
- Implement the issue's **Acceptance criteria** exactly. Respect **Out of scope** —
  do not add extras.
- Match the existing code style and patterns in nearby files.

## Never
- Never edit `.github/workflows/**`, this `CLAUDE.md`, or anything secret-related.
  If a task seems to need it, stop and ask on the issue.
- Never hardcode, invent, or guess secrets / credentials / API keys. They live in
  GitHub Actions secrets and the platform env — never in the repo.
- Never merge a PR. A human always clicks merge.

## If you are blocked
If you're missing a credential, an access grant, or a product decision you cannot
safely make on your own:
1. Post the **specific** question as a comment on the issue (one clear ask).
2. Add the label **`blocked`** to the issue.
3. **Stop.** Do not guess, do not fake access, do not ship a partial workaround.

(The `blocked` label sends a Slack ping to Abdulrahman.)
