# Handoff — multi-user backend (redesign-free) + pending Gmail sending

## What this branch (`multiuser-backend-only`) is
The **backend** of Khalid's multi-login PR (#3), extracted onto `main` **without
the light-theme UI redesign** (the current frontend keeps working as-is), plus
one change: **admin access is now restricted** (it was open to everyone).

### Multi-user model (how it works)
- **One shared API + DB, scoped per user.** No per-user endpoints.
- **Auth:** the platform proxy authenticates via Slack-OTP SSO and injects
  `X-Auth-Email`; `get_current_user` reads it and auto-creates a `User` row on
  first sign-in.
- **Leads are strictly per-user:** every leads endpoint (list, archive, and all
  id-addressed ones) filters on `owner_email == user.email` — no cross-user leak.
- **Assignment = Copper assignee → owner_email.** The 5-min sync (`_run_all`)
  loops every active user; for each it resolves their Copper user id via
  `lookup_user_id(email)` (matched by exact email, cached on `users.copper_user_id`),
  fetches the leads assigned to them in Copper, and imports them as
  `owner_email=<that user>`. Archiving is scoped per-user, so one person's
  reconcile never touches another's board.
- **Admin tabs** (Portfolio / Feedback / Overrides) are gated by `is_owner()`,
  now restricted to the **`ADMIN_EMAILS`** allow-list (defaults to `owner_email`).
  Set `ADMIN_EMAILS=a@raed.vc,b@raed.vc` to grant admin; everyone else still gets
  their own leads, just no admin tabs.

### ⚠️ Onboarding gotcha (per teammate)
A teammate's **Copper account email must equal their @raed.vc Slack login email**,
or `lookup_user_id` won't match and they get an **empty board**. Verify this for
each person (e.g. Walid) before expecting their leads to appear.

### Migrations on this branch
Chain is `i6c7d8e9f0a1` → `j7d8e9f0a1b2` (per-user owner_email, backfilled to
`abdulrahman@raed.vc`) → `k8e9f0a1b2c3` (user_onboarded_at). Head = `k8e9f0a1b2c3`.

### Deploy
Merging to `main` auto-deploys via the GitHub webhook; the migration runs on boot.
Keep work on the branch until verified. **Two-account smoke test before merge:**
sign in as two different @raed.vc users → each sees only their own leads, neither
can open the other's lead by ID.

---

## Pending: per-user Gmail-OAuth sending (NOT built)
Each user connects their own Google account once ("Connect Gmail"); outreach sends
through their real Gmail (From = `owner_email`; appears in their Sent). Internal
consent screen (all @raed.vc) → no Google app-review; scope `gmail.send` only.

**Prereq (console):** GCP project `pfund-3decb` — OAuth consent = Internal, scope
`gmail.send`, an **OAuth Web Client**, redirect URI
`https://deal-flow.apps.raed.vc/api/v1/gmail/oauth/callback` (+ a localhost one).
Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
4 deploy-form env vars (NOT the repo): `GOOGLE_OAUTH_CLIENT_ID`,
`GOOGLE_OAUTH_CLIENT_SECRET`, `GMAIL_OAUTH_REDIRECT_URI`, `GMAIL_TOKEN_ENC_KEY`.

**Build:**
1. `requirements.txt`: add `google-auth`, `google-auth-oauthlib`,
   `google-api-python-client`, pin `cryptography`.
2. `config.py`: the 4 settings + `is_gmail_oauth_configured()`.
3. New model `user_gmail_credentials` (`user_email` indexed, `google_email`,
   `encrypted_refresh_token` Text, `scopes`, `is_active`, timestamps) + register
   in `models/__init__.py`. **Migration must chain after `k8e9f0a1b2c3`** — use a
   NEW id, e.g. `l9f0a1b2c3d4`, `down_revision = "k8e9f0a1b2c3"` — with a partial
   unique index (`postgresql_where=is_active`) = one active connection per user.
4. `services/gmail_crypto.py`: Fernet encrypt/decrypt (refresh tokens never stored
   in plaintext).
5. `routers/gmail_oauth.py` (mounted `/api/v1/gmail`), `google_auth_oauthlib.flow.Flow`
   web flow: `GET /oauth/start` (auth'd; access_type=offline, prompt=consent,
   login_hint, signed+timestamped `state`); `GET /oauth/callback` (identity from
   validated state; `fetch_token`; **assert granted account == initiator** via Gmail
   `getProfile`, refuse on mismatch; encrypt + upsert; redirect `/?gmail=connected`);
   `GET /status`; `POST /disconnect`. Register in `main.py`.
6. `services/gmail_sender.py`: build `Credentials` from the decrypted refresh token
   (auto-refresh), MIME (From=sender), base64url, `users().messages().send`; run the
   blocking call via `anyio.to_thread.run_sync`. `GmailNotConnected` /
   `GmailTokenRevoked` (on RefreshError → flip inactive) errors.
7. Wire into `POST /assessments/{lead_id}/send`: sender = `lead.owner_email`; if the
   owner isn't connected → 412 `gmail_not_connected` (UI prompts Connect); else Gmail
   send; keep `_finalize_sent()` (Copper convert/archive) unchanged.
8. Frontend (later, with the redesign): a "Connect Gmail" chip + an inline Connect
   prompt in `EmailModal` on a 412.

**Verify (no spamming):** connect via `?fake_email=`; confirm the stored token is
Fernet ciphertext (`gAAAA…`); account-mismatch writes no row; send a disposable lead
whose `recipient_email` is your own/`+test` address → From = your @raed.vc, appears
in your Gmail **Sent** (proves Gmail-API, not SMTP), lead finalizes as before.

## Superseded
The earlier `feat/multi-user-leads` branch (an alternate multi-user implementation)
is retired in favour of this one; its `j7d8e9f0a1b2` migration would have collided.
