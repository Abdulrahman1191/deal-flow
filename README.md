# Raed Deal Flow

Internal AI deal-flow tool for Raed Ventures. Assesses MENA deep-tech leads from
Copper, drafts outreach emails, and tracks portfolio outcomes for thesis-tuning.

Deployed on the [Raed apps platform](https://auth.apps.raed.vc/deploy) at
**https://deal-flow.apps.raed.vc**.

## How this app fits the platform

- **Auth**: handled by the platform proxy (Slack OTP). Every request reaches us
  with `X-Auth-Email` set; we trust it and auto-create a `users` row on first
  contact. No login screen.
- **Subdomain + TLS**: provided by the platform.
- **Shared API keys**: `SLACK_BOT_TOKEN`, `COPPER_API_KEY`, `ANTHROPIC_API_KEY`,
  `GEMINI_API_KEY` are platform-injected.
- **App-specific env vars**: see [`.env.example`](.env.example) — the
  `[DEPLOY-FORM]` lines need to be added to the deploy form's env-vars panel.

## What's inside

```
├── backend/                FastAPI + SQLAlchemy + Celery + Alembic
│   ├── app/
│   │   ├── main.py         entry — also serves the built frontend
│   │   ├── routers/        API endpoints under /api/v1/*
│   │   ├── services/       LLM, S3, Copper, auth, etc.
│   │   ├── tasks/          Celery workers + beat schedules
│   │   ├── models/         SQLAlchemy ORM
│   │   └── ...
│   ├── alembic/            DB migrations — run on every container boot
│   ├── scripts/            One-shot CLI tools (bulk upload, etc.)
│   └── requirements.txt
├── frontend/               React + Vite + TanStack Query + Tailwind
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── api/            axios client
│   │   ├── lib/auth.ts     useMe() — calls /api/v1/auth/me
│   │   └── ...
│   ├── vite.config.ts      dev: proxies /api → :3000 (FastAPI)
│   └── package.json
├── Dockerfile              multi-stage: build frontend → run FastAPI
├── docker-compose.yml      platform-compliant: app + worker + redis
└── PLATFORM_MIGRATION_PLAN.md   full migration design + rationale
```

## Local dev

```bash
# 1. Copy + fill the env file
cp .env.example .env
# edit .env — at minimum DATABASE_URL, DEEP_SEEK_API, TAVILY_API_KEY

# 2. Boot the stack
APP_NAME=deal-flow docker compose up

# 3. Visit the app with a fake identity (no Slack login locally)
open http://localhost:3000/?fake_email=you@raed.vc
```

The `?fake_email=` querystring stands in for the platform's `X-Auth-Email`
header during local dev. Frontend remembers it via `localStorage.setItem`:

```js
localStorage.setItem("fake_email", "you@raed.vc");
```

## Drive sync (one-time setup for the 427 pitch decks)

The 427 PDFs live in a shared Google Drive folder (ID `1XN96GRGwxJCk23GiY6o9PY27u30lbpEk`).
Running `backend/scripts/sync_drive_to_db.py` matches each PDF to a lead row
and stores the Drive file ID. The "View PDF" button then redirects to
`drive.google.com/file/d/<id>/view`.

See **[DRIVE_OAUTH_SETUP.md](DRIVE_OAUTH_SETUP.md)** for the one-time Google
Cloud OAuth client setup, then run:

```bash
export DRIVE_PITCH_DECK_FOLDER_ID=1XN96GRGwxJCk23GiY6o9PY27u30lbpEk
export DATABASE_URL=<platform DB URL>
python backend/scripts/sync_drive_to_db.py
```

## Production deploy

1. Click **Use this template** on https://github.com/KhalidAlMuhammed/app-starter
   if you haven't already; this repo *is* that template applied.
2. Visit https://auth.apps.raed.vc/deploy
3. Fill the form:
   - **App name**: `deal-flow`
   - **Git clone URL**: `https://github.com/Abdulrahman1191/deal-flow.git`
   - **Env vars**: copy from `.env.example`, fill the `[DEPLOY-FORM]` lines
4. Khalid provides `DATABASE_URL`, `COPPER_WEBHOOK_SECRET` via Slack
5. Hit **Deploy** → wait for build log → app comes live at the subdomain
6. First time: ping Khalid to load the DB dump (see migration plan)

## Things that aren't standard FastAPI

- **`app/main.py` serves the React frontend too** — the Dockerfile builds
  `frontend/dist/` in stage 1 and mounts it at `/`. There's an SPA fallback
  so React Router works for non-API paths.
- **Auth is header-trust, not JWT** — `app/services/auth.py::get_current_user`
  reads `X-Auth-Email` set by the platform proxy. The legacy `/auth/login`
  returns 410 Gone with a pointer to the platform login URL.
- **Pitch deck PDFs live in a shared Google Drive folder** — `/leads/{id}/pitch-deck`
  returns a 307 redirect to `https://drive.google.com/file/d/<id>/view`. The
  Drive folder is shared with `@raed.vc` so platform-authenticated users can
  view the PDF directly in Drive's native viewer. No file bytes flow through
  our backend. To map Drive files to leads, run
  `python scripts/sync_drive_to_db.py` locally (one-shot OAuth flow).
- **Migrations auto-run on boot** — the entrypoint does
  `alembic upgrade head && uvicorn ...`. Safe + idempotent.

## Background work

Celery worker + beat scheduler run in the `worker` service. Two things are
scheduled by default:

- Daily briefing generation at the cron time in `BRIEFING_CRON_*`
- Periodic Copper outbox drain (every 30s)

The periodic `sync_copper_leads_task` is **disabled by default** behind
`DISABLE_COPPER_SYNC=true` — flip it off when you want webhook-driven Copper
import.

## When something breaks

- App won't start: `docker compose logs app` — usually a missing env var
- DB errors on boot: `alembic upgrade head` failed; check `DATABASE_URL`
- "Not signed in" panel: in production, the platform proxy didn't gate the
  request (check with Khalid). In dev, set `localStorage.fake_email` and reload.
- Pitch deck shows 503: the lead has a `pitch_deck_filename` but no `pitch_deck_drive_id` yet — run `scripts/sync_drive_to_db.py`
- Owner-only tabs missing: `OWNER_EMAIL` env var doesn't match your Slack email
