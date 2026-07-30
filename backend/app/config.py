"""
App configuration — platform-targeted edition.

All values come from environment variables (the platform injects shared keys;
the deploy form lets us add app-specific ones). Local dev reads from .env.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- LLM providers ---
    deep_seek_api: str = ""              # primary assessment model
    deepseek_model: str = "deepseek-chat"
    # Platform also injects ANTHROPIC_API_KEY and GEMINI_API_KEY — we don't
    # currently use them but they're available if we want to swap models.

    # --- Web research ---
    tavily_api_key: str = ""

    # --- Copper CRM ---
    copper_webhook_secret: str = ""
    copper_api_key: str = ""             # provided by the platform
    copper_user_email: str = ""
    copper_user_id: int = 0
    copper_open_status_id: int = 0
    copper_unqualified_status_id: int = 0
    copper_pipeline_id: int = 0
    copper_pipeline_stage_id: int = 0

    # Copper custom-field IDs (one-time setup per COPPER_BIDIRECTIONAL_SYNC.md §3)
    copper_cf_draft_subject_id: int = 0
    copper_cf_draft_body_id: int = 0
    copper_cf_draft_type_id: int = 0
    copper_cf_summary_id: int = 0
    copper_cf_app_status_id: int = 0
    # AI-generated reason(s) (MultiSelect) + detail (Text) for why a lead was
    # unqualified — written alongside status_id when a lead archives/rejects.
    copper_cf_unqual_reason_id: int = 0
    copper_cf_unqual_detail_id: int = 0
    # [URL] "Pitch Deck" field (Copper CF id 757961 in prod) -- the sanctioned
    # in-Copper channel for attaching a deck, since Copper's own file
    # attachments aren't downloadable via its API. 0 = disabled/no-op.
    copper_cf_pitch_deck_url_id: int = 0

    # Prior-contact detection (issue #90): how often (in days) to re-fetch a
    # lead's Copper activity feed to refresh prior_contact/_count/_last_at.
    # Activity history rarely changes once set, so we don't hit the
    # activities API on every 5-min sync cycle for every lead.
    prior_contact_refresh_days: int = 7

    # --- Storage ---
    database_url: str                    # injected by platform/Khalid
    redis_url: str = "redis://redis:6379/0"

    # --- Outbound email (SES or SendGrid via SMTP) ---
    # Both providers expose SMTP, so one generic config works for either.
    # SendGrid: smtp_host=smtp.sendgrid.net, smtp_username="apikey", smtp_password=<API key>.
    # SES:      smtp_host=email-smtp.<region>.amazonaws.com, smtp_username/password = SES SMTP creds.
    # mail_from MUST be a verified sender/domain (e.g. deals@raed.vc). Sending is
    # disabled (the /send endpoint returns 503) until smtp_host + mail_from are set.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    mail_from: str = ""
    mail_from_name: str = "Raed Ventures"

    # --- Google Drive (pitch decks) ---
    # The Drive folder containing the lead pitch decks. We don't need Google
    # credentials at runtime — the view endpoint redirects to
    # https://drive.google.com/file/d/<id>/view and Drive enforces access via
    # the signed-in user's Google session. The folder ID is only used by the
    # scripts/sync_drive_to_db.py backfill (which DOES need OAuth, but only
    # runs locally when an admin wants to refresh the Drive→DB mapping).
    drive_pitch_deck_folder_id: str = ""
    # Service account JSON key (as a raw JSON string) used by the scheduled
    # sync_pitch_decks task to list/download files from the folder above.
    # Unset in dev/until a maintainer adds it post-merge — the task no-ops
    # gracefully rather than crashing the worker when this is empty.
    google_service_account_json: str = ""

    # --- Pitch-deck match verification (issue #74) ---
    # Filenames scoring below MATCH_THRESHOLD (0.85) but at/above this floor
    # are candidates for the LLM content-verification tier -- real matches
    # lost to transliteration/bilingual naming live in this band (~0.6-0.85);
    # genuinely different companies score below it. See
    # app/services/pitch_deck.py find_lead_match / verify_match_candidates.
    deck_match_fuzzy_floor: float = 0.6
    # Gate for the whole verification tier. Off -> near-miss/ambiguous
    # filenames stay unmatched exactly as before this feature (no LLM calls,
    # no behavior change to the existing high-confidence auto-attach path).
    deck_match_verify_enabled: bool = True

    # --- Pitch-deck OCR fallback (issue #97) ---
    # Gate for the whole OCR fallback tier in extract_text_from_pdf. Off ->
    # scanned/image-only decks are left ungarbled-but-empty exactly as before
    # this feature (no Tesseract calls), useful if a deploy image is missing
    # the tesseract binary/traineddata and OCR attempts would just spam logs.
    pitch_deck_ocr_enabled: bool = True

    # --- Daily briefing schedule ---
    briefing_cron_hour: int = 4
    briefing_cron_minute: int = 0

    # --- Orphaned-assessment reaper (issue #100) ---
    # A lead stuck in 'processing' or 'pending' with updated_at older than this
    # many minutes is treated as orphaned (worker crash/restart/OOM lost the
    # task) and re-enqueued by reap_stuck_leads_task. Must stay comfortably
    # above a normal assessment's runtime so an in-flight lead is never reaped.
    assessment_reap_after_minutes: int = 20

    # --- Owner / identity ---
    # Email that gets owner-level access to Portfolio + Feedback tabs.
    # On the platform, this is the @raed.vc identity. Falls back to legacy
    # value for backwards-compat with the Lightsail deployment during cutover.
    owner_email: str = "abdulrahman@raed.vc"
    associate_name: str = "Abdulrahman"
    # Comma-separated allow-list of emails with ADMIN access (Portfolio +
    # Feedback + Overrides tabs). Empty → just `owner_email`. NOTE: per-user
    # LEAD visibility is independent of this — every user always sees only their
    # own leads regardless of admin status.
    admin_emails: str = ""
    # Comma-separated list of @raed.vc emails to pre-provision ahead of first
    # sign-in, so the periodic Copper sync can populate their board before
    # they ever log in. Empty → just `owner_email` (see team_email_list()).
    team_emails: str = ""

    # --- Misc behavioural flags ---
    # Skip the periodic Copper sync task. Useful when bulk-pruning leads or
    # during DB migrations to avoid re-importing rows.
    disable_copper_sync: bool = False
    # Cap how many Copper leads the sync imports (0 = no cap). Referenced by
    # sync_copper; was previously undefined, which crashed the task on every run.
    test_lead_limit: int = 0

    # --- UptimeRobot (optional; vestigial, kept to avoid pydantic strict mode) ---
    uptimerobot_main_api_key: str = ""

    def admin_email_set(self) -> set[str]:
        """Lowercased set of admin emails. Defaults to just `owner_email` when
        ADMIN_EMAILS is unset — so admin is never accidentally granted to all."""
        raw = [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]
        return set(raw) if raw else {self.owner_email.strip().lower()}

    def team_email_list(self) -> list[str]:
        """Lowercased, deduplicated list of TEAM_EMAILS to pre-provision.
        `owner_email` is always included even if not explicitly listed."""
        raw = [e.strip().lower() for e in self.team_emails.split(",") if e.strip()]
        emails = list(dict.fromkeys(raw))
        owner = self.owner_email.strip().lower()
        if owner not in emails:
            emails.append(owner)
        return emails

    class Config:
        env_file = ".env"
        case_sensitive = False
        env_ignore_empty = True


settings = Settings()
