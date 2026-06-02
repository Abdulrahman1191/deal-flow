# Drive OAuth Setup — for `scripts/sync_drive_to_db.py`

This is a **one-time** setup on your laptop so the sync script can read the
list of files in the shared Drive folder. After the first run, the token is
cached and the script runs silently.

The OAuth credentials live **on your laptop only**, never in the platform
deployment. The platform app doesn't need any Google credentials — it just
redirects users to `drive.google.com/file/d/<id>/view` and Drive handles
access via the user's Slack-signed Google session.

## What we're doing

You're creating a free "OAuth client" inside a (free) Google Cloud project.
The script uses that client to ask Google for permission to read your Drive
folder list. Permission is scoped to **read-only metadata only** — the script
literally cannot edit or delete anything in Drive.

## 5-minute walkthrough

### 1. Create / pick a Google Cloud project

1. Open https://console.cloud.google.com — sign in with your `@raed.vc` account
2. Top bar dropdown → **New Project** → name it `raed-deal-flow` → **Create**
3. Wait ~10 seconds for it to be created, then select it from the top dropdown

### 2. Enable the Drive API

1. In the search bar at the top, type **"Google Drive API"**
2. Click the result → **Enable** (takes a few seconds)

### 3. Configure the OAuth consent screen (only first time, for this project)

1. Left nav → **APIs & Services** → **OAuth consent screen**
2. **User Type: Internal** → **Create**
   *(Internal = anyone with a `@raed.vc` Google Workspace account. Won't appear in any public marketplace.)*
3. Fill the required fields:
   - **App name**: `raed-deal-flow`
   - **User support email**: your `@raed.vc` email
   - **Developer contact**: same email
4. Click **Save and continue** through the next screens — you can skip "Scopes" and "Test users" without filling anything

### 4. Create the OAuth client

1. Left nav → **APIs & Services** → **Credentials**
2. **+ Create Credentials** (top of page) → **OAuth client ID**
3. **Application type: Desktop app**
4. **Name**: `raed-deal-flow CLI` (any name works — only you see it)
5. **Create**
6. A popup appears with the credentials. Click **Download JSON**

### 5. Move the JSON to where the script expects it

```bash
mkdir -p ~/.config/raed-deal-flow
mv ~/Downloads/client_secret_*.json ~/.config/raed-deal-flow/oauth-credentials.json
```

### 6. (Once Khalid sends the DB URL) Run the sync

```bash
cd /path/to/deal-flow-staging
pip install google-auth google-auth-oauthlib google-api-python-client

export DRIVE_PITCH_DECK_FOLDER_ID=1XN96GRGwxJCk23GiY6o9PY27u30lbpEk
export DATABASE_URL="<the platform DB URL Khalid sent you>"

python backend/scripts/sync_drive_to_db.py --dry-run    # see what would change
python backend/scripts/sync_drive_to_db.py              # commit the matches
```

First run pops a browser tab → you confirm the `@raed.vc` account → done.
Token is cached at `~/.cache/raed-deal-flow/drive-token.json`. Subsequent
runs are silent.

## What to expect from the dry run

```
[1/3] Listing PDFs in Drive folder 1XN96GRGwxJCk23GiY6o9PY27u30lbpEk…
      found 427 PDFs
[2/3] Loading leads from DB…
      ~400 leads in DB
[3/3] Matching + writing…

Done. ~380 new mappings, 0 overwritten, 0 already up-to-date, ~50 Drive files matched no lead.
(dry run — no DB writes)
```

The "matched no lead" count is expected — these are usually older decks for
companies that aren't in our current pipeline. The fuzzy matcher uses
`difflib.get_close_matches` at cutoff 0.82 (tuned for typical Copper name
variations).

## Troubleshooting

| Error | Fix |
|---|---|
| `OAuth client credentials not found at ~/.config/raed-deal-flow/oauth-credentials.json` | You skipped step 5 — move the downloaded JSON there. |
| `Access blocked: This app's request is invalid` after sign-in | OAuth consent screen wasn't set to **Internal** — go back to step 3, switch user type. |
| `Missing Google API client libraries` | `pip install google-auth google-auth-oauthlib google-api-python-client` |
| `403 Forbidden` listing the folder | You're signed in as the wrong account — the OAuth flow caches the choice; delete `~/.cache/raed-deal-flow/drive-token.json` and retry. |
| `0 new mappings, 427 matched no lead` | Folder ID points at the wrong folder, OR the Drive folder has subfolders (script is non-recursive — drop the PDFs flat at the top level). |
