# Platform Migration Plan — Lightsail → apps.raed.vc

> Created: 2026-05-19
> Owner: Abdulrahman Alhashim
> Platform admin: Khalid Al-Muhammed (Slack)

## Decisions locked

| | |
|---|---|
| Subdomain | `deal-flow.apps.raed.vc` |
| App name (kebab-case) | `deal-flow` |
| Owner email (replaces `associate@raedventures.com`) | `abdulrahman@raed.vc` |
| Database | Handled by Khalid via the deploy form — we provide a `pg_dump` artifact |
| Pitch deck storage | Google Drive (shared folder), `/leads/{id}/pitch-deck` redirects to drive.google.com |
| Auth | Platform-provided Slack OTP; backend trusts `X-Auth-Email` request header |
| Local-dev auth fallback | `?fake_email=<addr>` querystring (matches starter convention) |

## What the platform gives us

- HTTPS at `https://deal-flow.apps.raed.vc`
- Slack-OTP login on every request → app receives `X-Auth-Email`, `X-Auth-Slack-Id`, `X-Auth-Name` headers
- Shared API keys injected at deploy: `SLACK_BOT_TOKEN`, `COPPER_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`
- Web-form deploys at https://auth.apps.raed.vc/deploy
- Auto rebuild on form re-submission

## What the platform forces

- `services: app:` exact service name in compose
- `container_name: ${APP_NAME}-app`
- Joined to external `raed_platform` network
- No host port publishing — proxy reaches us over the platform network
- Listen on `$PORT`
- No persistent local volumes (need external object storage)
- Reject requests without `X-Auth-Email` in production (defense in depth)

## Architecture deltas (current → platform-ready)

| Concern | Current (Lightsail) | After migration |
|---|---|---|
| Frontend serving | Caddy (own container) | FastAPI serves `dist/` directly (StaticFiles + SPA fallback) |
| HTTPS | Caddy + raw IP | Platform proxy (free) |
| Reverse proxy | Caddy | Platform |
| Auth | JWT + bcrypt + `/auth/login` | `X-Auth-Email` trust + Slack OTP at proxy |
| DB | Postgres in compose | Khalid-provisioned, connection string in env |
| Redis | In compose | Stays in compose (additional service allowed) |
| Celery worker + beat | One container, solo pool | Same, separate service alongside `app` |
| Pitch decks | 818 MB on `/opt/raed/pitch-decks/` volume | Shared Drive folder; app stores Drive file IDs |
| User identity | Email/password row in `users` table | Auto-create on first request from any `@raed.vc` email |
| Owner check | `OWNER_EMAIL = "associate@raedventures.com"` | `OWNER_EMAIL = "abdulrahman@raed.vc"` |
| Webhook ingestion | Cowork POSTs to `/api/v1/leads/ingest` with HMAC | Same code; needs platform-proxy path-bypass (ask Khalid) OR move to polling reconciler |

## Repo layout in the platform-ready repo

```
deal-flow/                          # new repo created from KhalidAlMuhammed/app-starter template
├── README.md                       # how to run/deploy
├── Dockerfile                      # multi-stage: frontend build + python runtime
├── docker-compose.yml              # platform-compliant (app, worker, redis services)
├── .dockerignore
├── backend/                        # our existing FastAPI code, slightly adapted
│   ├── app/
│   ├── alembic/
│   ├── requirements.txt
│   └── scripts/
└── frontend/                       # our existing React/Vite code, built into dist/ during docker build
    ├── src/
    ├── package.json
    └── vite.config.ts
```

## Env vars to declare at deploy time

**Platform-provided (shared, no action needed):**
- `SLACK_BOT_TOKEN`
- `COPPER_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`

**App-specific (Khalid needs to inject — list provided separately):**
- `DATABASE_URL` — provided by Khalid after DB load
- `REDIS_URL` — `redis://redis:6379/0` (in-compose service)
- `DEEPSEEK_API_KEY` — we keep using DeepSeek for assessments
- `TAVILY_API_KEY` — web research
- `COPPER_USER_EMAIL`, `COPPER_USER_ID`, `COPPER_OPEN_STATUS_ID`, `COPPER_UNQUALIFIED_STATUS_ID`, `COPPER_PIPELINE_ID`, `COPPER_PIPELINE_STAGE_ID` — Copper IDs already in the existing `.env`
- `COPPER_WEBHOOK_SECRET` — for HMAC verification on inbound webhooks
- `DRIVE_PITCH_DECK_FOLDER_ID` — Drive folder ID (no Google creds in app env; access is enforced by Drive sharing)
- `OWNER_EMAIL=abdulrahman@raed.vc`
- `ENV=prod`
- `DISABLE_COPPER_SYNC=true` (or remove once we figure out webhooks)

**Dropped (no longer needed):**
- `JWT_SECRET_KEY` — auth is now proxy-driven
- `JWT_ALGORITHM`, `JWT_EXPIRY_HOURS`
- `PITCH_DECK_INBOX` — no local folder anymore
- `UPTIMEROBOT_MAIN_API_KEY` — UptimeRobot was monitoring the old box

## Open questions for Khalid (to send via Slack)

1. **Database**: how do you want the `pg_dump`? Slack DM, S3 link, GitHub release attachment, something else?
2. **Webhook auth**: the current app receives HMAC-signed POSTs from Copper at `/api/v1/leads/ingest` (no Slack identity). Two ways forward:
   - a. Platform proxy bypasses auth for that specific path (preferred — keeps the existing real-time sync)
   - b. We drop the webhook and replace with a polling reconciler that runs inside the worker container every 5 min (simpler, slightly delayed)
3. **App-specific env vars**: can you inject `DEEPSEEK_API_KEY`, `TAVILY_API_KEY`, `AWS_*` from a per-app secret store? If yes, what's the workflow?
4. **Redis-as-additional-service** in our compose — confirmed OK?
5. **Worker container** — confirmed OK? Doc says yes ("Build them as a separate worker service in your docker-compose.yml").

## What user does

1. Click "Use this template" on https://github.com/KhalidAlMuhammed/app-starter → name it `deal-flow` → create the repo on your GitHub
2. Paste the repo URL here → I push the adapted code to it
3. Send Khalid the Slack questions above
4. Once Khalid confirms the DB connection string + accepts the pg_dump, submit the deploy form at https://auth.apps.raed.vc/deploy

## What I do (in order)

| # | Step | Done? |
|---|---|---|
| 1 | This plan doc | 🟡 |
| 2 | Multi-stage Dockerfile (frontend build + python runtime) | ☐ |
| 3 | Platform-compliant docker-compose.yml | ☐ |
| 4 | Rewrite `get_current_user` for `X-Auth-Email` | ☐ |
| 5 | Mount React `dist/` from FastAPI + SPA fallback | ☐ |
| 6 | Add `/api/v1/me` endpoint | ☐ |
| 7 | Drive-redirect pitch deck endpoint + Drive→DB backfill script | ✅ |
| 8 | pg_dump of current DB → store at `/tmp/raed-deal-flow.dump` ready for handoff | ☐ |
| 9 | Local docker compose smoke test with `?fake_email=` | ☐ |
| 10 | Push to the new repo + send user the deploy-form payload | ☐ |

## Cutover strategy

The existing AWS Lightsail deployment **stays running** until the new one is verified. Specifically:

1. Build + deploy `deal-flow.apps.raed.vc` (no production traffic yet)
2. Smoke test: login as me, check Portfolio tab, override a lead, send a draft, view a pitch deck
3. If healthy: switch Cowork webhook target from `http://13.206.104.31/api/v1/leads/ingest` to the new URL (once we have the bypass)
4. Watch for 24h
5. Tear down the Lightsail box: `aws lightsail delete-instance --region ap-south-1 --instance-name raed-test-server --force-delete-add-ons` (and release the static IP)

## Out of scope for this migration

- LLM tuning work — the prompt + research pipeline stays as-is, just runs on the new infra
- The portfolio template + bulk-import — works the same once the new env is up
- The Copper custom field setup (F8 + F9 still gated on those IDs being configured)
- UptimeRobot monitor — needs re-pointing at the new URL once we have it
