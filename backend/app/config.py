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

    # --- Daily briefing schedule ---
    briefing_cron_hour: int = 4
    briefing_cron_minute: int = 0

    # --- Owner / identity ---
    # Email that gets owner-level access to Portfolio + Feedback tabs.
    # On the platform, this is the @raed.vc identity. Falls back to legacy
    # value for backwards-compat with the Lightsail deployment during cutover.
    owner_email: str = "abdulrahman@raed.vc"
    associate_name: str = "Abdulrahman"

    # --- Misc behavioural flags ---
    # Skip the periodic Copper sync task. Useful when bulk-pruning leads or
    # during DB migrations to avoid re-importing rows.
    disable_copper_sync: bool = False

    # --- UptimeRobot (optional; vestigial, kept to avoid pydantic strict mode) ---
    uptimerobot_main_api_key: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False
        env_ignore_empty = True


settings = Settings()
