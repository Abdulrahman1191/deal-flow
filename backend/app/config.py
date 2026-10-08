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
    # Platform also injects ANTHROPIC_API_KEY — available if we want to swap
    # the assessment model.
    # Gemini is used for pitch-deck text extraction (app/services/deck_llm.py):
    # it reads the PDF directly, including scanned/Arabic decks that DeepSeek
    # cannot see because it is text-only.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.7-flash"

    # --- Web research ---
    tavily_api_key: str = ""

    # --- Copper CRM ---
    copper_webhook_secret: str = ""
    # Public URL Copper should POST lead webhook events to -- normally
    # https://deal-flow.apps.raed.vc/api/v1/leads/ingest (issue #174).
    # scripts/register_copper_webhooks.py refuses to run without this set,
    # rather than registering a subscription that points nowhere useful.
    copper_webhook_target_url: str = ""
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
    # [String] "Source detail" field -- holds the original inbound email
    # subject line, which is often the applicant's own Arabic text even when
    # our `description` enrichment is an English AI/team summary. Fed into
    # claude_agent.detect_applicant_language (issue #168) as applicant-authored
    # signal. 0 = disabled/no-op (detection degrades silently to today's
    # behaviour, i.e. company_name + description + pitch_deck_text only).
    copper_cf_source_detail_id: int = 244394
    # Application inbox address (issue #170): when "Source detail" reads
    # "Emailed <address>: <subject>" and <address> is this inbox (or the
    # field just begins with "Emailed"), a deck-less new lead skips the
    # awaiting_deck grace period entirely and is assessed immediately --
    # founders emailing the inbox directly usually attach nothing and never
    # will, so waiting for a Drive deck is pointless for them. See
    # app/tasks/sync_copper.py _is_email_sourced.
    application_inbox_email: str = "info@raed.vc"
    # Copper's structured "Lead Source" field (customer_source_id) -- used
    # only as a fallback for deriving Lead.source when "Source detail" is
    # blank (issue #217), since it's applied inconsistently in practice (963
    # "Inbound - Direct" vs. 823 "Emailed ..." Source detail values measured
    # on the same leads). This is the id for "Inbound - Info/Website" in our
    # account; any other customer_source_id falls back to "unknown" rather
    # than guessing. See app/services/copper_service.derive_lead_source.
    copper_customer_source_id_website_form: int = 1444487

    # Prior-contact detection (issue #90): how often (in days) to re-fetch a
    # lead's Copper activity feed to refresh prior_contact/_count/_last_at.
    # Activity history rarely changes once set, so we don't hit the
    # activities API on every 5-min sync cycle for every lead.
    prior_contact_refresh_days: int = 7

    # How many days a freshly-imported deck-less lead waits in `awaiting_deck`
    # before promote_awaiting_deck.py falls it back to the #144 website/
    # description assessment (issue #149). Gives a deck that's about to be
    # uploaded to the Drive folder a chance to be used instead of a premature
    # deck-less verdict. Shortened 5 -> 2 (issue #170): live evidence showed
    # 32/34 awaiting_deck leads arrived by email and will never receive a
    # deck, so a long grace period only delays their fallback assessment.
    deck_grace_period_days: int = 2
    # Caps how many times promote_awaiting_deck.py may re-park a deck-less
    # lead (leads.deck_promotion_count) before assess_lead._run gives up and
    # writes a MAYBE placeholder card instead of re-parking again (issue
    # #170). Without this, a lead with genuinely no usable context (no deck,
    # no website, blank description) got a fresh grace period on every
    # promotion and cycled through awaiting_deck forever with no signal to
    # the partner. Never REJECTs for absent data (issue #147 rule still
    # holds) -- the placeholder bucket is always MAYBE.
    max_deck_promotions: int = 2

    # --- Storage ---
    database_url: str                    # injected by platform/Khalid
    redis_url: str = "redis://redis:6379/0"
    # Caller authentication beyond the platform proxy (app/services/auth.py
    # caller_gate). DEALFLOW_SERVICE_TOKEN: shared with Reem; a direct call
    # carrying it may assert X-Auth-Email, on SERVICE_ROUTES only.
    # RAED_PROXY_SECRET: the header value the platform proxy adds to every
    # proxied request; when set, X-Auth-Email is only trusted alongside it
    # (or a valid service token). Both empty = previous behaviour.
    dealflow_service_token: str = ""
    raed_proxy_secret: str = ""
    # After DeepSeek answers 402 (no balance) or 401 (bad key), pause every
    # DeepSeek call fleet-wide for this long before probing again
    # (app/services/llm_breaker.py).
    llm_outage_pause_seconds: int = 900
    # Where an operations fault pages its owner: reem's POST /internal/ops/alert
    # (app/services/ops_alert.py), with reem's internal token:
    # http://reem:4000/internal/ops/alert over the raed_platform network, which
    # the app and both workers join (the breaker trips in the workers). Either
    # empty = the page is printed to the log only.
    reem_ops_alert_url: str = ""
    reem_internal_token: str = ""
    # llm_usage rows (one per DeepSeek call, issue #191) older than this are
    # purged by the nightly dedupe-leads sweep (app/tasks/dedupe_leads.py) so
    # the table doesn't grow unbounded.
    llm_usage_retention_days: int = 90
    # Max leads a single POST /leads/bulk-reassess call may queue (issue
    # #203). Queueing an unbounded batch into a single request is how a
    # fat-fingered "reassess the whole board" would turn into hundreds of
    # DeepSeek calls (~12k input tokens each) in one shot; split a bigger
    # pile into multiple calls instead.
    bulk_reassess_batch_cap: int = 250

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
    # How many seconds apart POST /leads/bulk-send-rejection spaces each
    # lead's send (issue #205): each lead is its own Celery task, dispatched
    # with an increasing countdown of index * this value, rather than firing
    # the whole batch at once. Gmail's daily send cap and spam heuristics both
    # penalize a burst from a single address.
    bulk_rejection_send_interval_seconds: float = 2.0

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

    # Second, additive source folder (issue #189): the info@ intake agent's
    # "Inbound Pitch Decks" folder, where every email attachment lands
    # automatically all day -- as opposed to drive_pitch_deck_folder_id above,
    # which a partner still populates by hand. Swept IN ADDITION to that
    # folder, never instead of it. "" (default) = today's behaviour exactly;
    # nothing about the existing folder sweep changes when this is unset.
    # Strictly read-only: sync_pitch_decks.py must never rename/move/delete
    # anything here, since it's owned by almuhammed@raed.vc and shared with
    # the whole team.
    drive_inbound_deck_folder_id: str = ""
    # Files in the inbound folder are named "<sender domain> — <original
    # filename>.pdf" by the intake agent. How many days before an unmatched
    # file is re-checked (a lead may arrive in Copper after its deck did) --
    # see app.tasks.sync_pitch_decks._sweep_inbound_folder.
    inbound_deck_recheck_days: int = 30
    # Sender domains that carry no identifying signal (free/webmail
    # providers used by many unrelated applicants) -- ignored rather than
    # matched against a lead's website/contact-email domain. Configurable so
    # a new generic provider can be added without a code change.
    drive_generic_sender_domains: str = (
        "gmail.com,googlemail.com,hotmail.com,hotmail.co.uk,outlook.com,"
        "yahoo.com,icloud.com,aol.com,live.com,msn.com"
    )

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
    # How long a cached verify_match_candidates verdict (see
    # app/services/deck_verification_cache.py, issue #192) stays valid before
    # the next sweep re-verifies regardless. 30 days comfortably outlasts the
    # 30-minute sweep interval while still catching a stale verdict should
    # the model/prompt ever change.
    deck_verification_cache_ttl_days: int = 30

    # --- Deck-sweep minimum interval ---
    # Both deck sweeps run on a 1800s beat schedule. Beat enqueues them on that
    # interval regardless of whether the previous one is still queued, and the
    # workers are --pool=solo, so a sweep that runs longer than its interval
    # makes the `heavy` queue grow without bound: 59 sync_pitch_decks and 40
    # sync_copper_pitch_deck_links tasks were queued on 2026-08-21, ~29 hours
    # of accumulation. A duplicate sweep re-lists the same Drive folder and
    # re-verifies the same unmatched filenames, so it is waste, not redundancy.
    #
    # Below the 1800s schedule on purpose: a legitimately-scheduled run must
    # never be skipped for clock jitter or a slightly-early beat tick. Set to 0
    # to disable the guard entirely. See app/services/task_guard.py.
    #
    # (issue #149) Kept at 1800s rather than shortened: a freshly-uploaded
    # deck is picked up within ~30 minutes worst case, and the 2026-08-21
    # incident above is exactly what shortening this interval would risk
    # reproducing. 30 minutes is a fine latency against a multi-day
    # deck_grace_period_days grace period.
    deck_sweep_min_interval_seconds: int = 1500

    # --- Pitch-deck LLM extraction (replaces the OCR fallback of issue #97) ---
    # Gate for the multimodal-LLM tier in extract_text_from_pdf (app/services/
    # deck_llm.py). Off -> scanned/image-only decks stay empty and get flagged,
    # exactly as they did before any fallback tier existed.
    #
    # This replaced Tesseract OCR on 2026-08-18. OCR rendered 40 pages at 300
    # DPI and ran ara+eng over them: minutes of multi-core CPU per deck inside a
    # --pool=solo worker, which blocked every other task behind it. Gemini does
    # the same job over the network, so the worker waits on a socket instead of
    # burning prod's cores.
    pitch_deck_llm_extraction_enabled: bool = True

    # --- Daily briefing schedule ---
    briefing_cron_hour: int = 4
    briefing_cron_minute: int = 0

    # --- Orphaned-assessment reaper (issue #100) ---
    # A lead stuck in 'processing' or 'pending' with updated_at older than this
    # many minutes is treated as orphaned (worker crash/restart/OOM lost the
    # task) and re-enqueued by reap_stuck_leads_task. Must stay comfortably
    # above a normal assessment's runtime so an in-flight lead is never reaped.
    assessment_reap_after_minutes: int = 20

    # --- Failed-assessment auto-recovery (issue #163) ---
    # A lead dead-lettered to 'failed' (MAX_ASSESS_ATTEMPTS exceeded, or every
    # Celery retry exhausted) would otherwise sit there forever -- nothing
    # else ever re-queues it (reap_stuck_leads.py explicitly excludes
    # 'failed'). redrive_failed_assessments_task resets eligible failed leads
    # back to 'pending' at most this many times each (tracked per-lead via
    # leads.assessment_failed_redrives) so a permanently broken lead can't
    # loop forever; past the cap it stays 'failed' for good, still visible
    # via GET /leads/failed-summary.
    assessment_failed_max_redrives: int = 3

    # --- Copper outbox re-drive (issue #131) ---
    # A Copper write-back that exhausts drain_outbox's 5 delivery attempts
    # lands in status='failed' and would otherwise never be retried again --
    # a transient Copper outage permanently strands it. redrive_failed_outbox_task
    # resets failed rows back to 'pending' at most this many times each
    # (tracked per-row via copper_outbox.redrive_count); rows past the cap
    # stay 'failed' for good (genuinely undeliverable), still visible via
    # GET /leads/outbox-health (issue #65).
    outbox_max_redrives: int = 3
    # How often (seconds) redrive_failed_outbox_task runs -- see the
    # "redrive-failed-copper-outbox" beat schedule entry in celery_app.py.
    outbox_redrive_interval_seconds: int = 1800  # 30 minutes

    # --- Ownership reconcile cadence (issue #171) ---
    # How often (seconds) reconcile_ownership_task runs. This is the fallback
    # safety net for Copper reassignments -- the webhook `update` branch
    # (app/routers/leads.py) now handles reassignment immediately, so this
    # cadence only bounds the worst case when a webhook is missed/unregistered.
    # Lowered from the original hard-coded 900s (issue #123) to 300s now that
    # it's a backstop rather than the primary mechanism.
    ownership_reconcile_interval_seconds: int = 300

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
    # Comma-separated subset of TEAM_EMAILS that are valid users but not
    # client-facing associates (test/engineer accounts) -- excluded from
    # associate-facing views (GP dashboard, view-as dropdown, per-associate
    # reporting) while still getting a user row + Copper sync like everyone
    # else. See client_facing_email_list(). Onboard/offboard a temporary
    # tester by adding/removing them in TEAM_EMAILS (+ redeploy); use this
    # setting only to keep a permanent non-client-facing account (like the
    # QA account below) out of those views.
    non_client_facing_emails: str = "almuhammed@raed.vc"

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

    def generic_sender_domain_set(self) -> set[str]:
        """Lowercased set of DRIVE_GENERIC_SENDER_DOMAINS -- webmail domains
        that must never be treated as an identifying match signal (see
        app/services/pitch_deck.py find_lead_match's `sender_domain` param)."""
        return {d.strip().lower() for d in self.drive_generic_sender_domains.split(",") if d.strip()}

    def non_client_facing_email_set(self) -> set[str]:
        """Lowercased set of NON_CLIENT_FACING_EMAILS -- test/engineer
        accounts that are valid users but excluded from associate-facing
        views."""
        return {e.strip().lower() for e in self.non_client_facing_emails.split(",") if e.strip()}

    def client_facing_email_list(self) -> list[str]:
        """team_email_list() minus non_client_facing_email_set() -- the
        data-driven roster for associate-facing views (GP dashboard, view-as
        dropdown, per-associate reporting). Adding/removing an email from
        TEAM_EMAILS adds/removes them here automatically; no hardcoded
        associate list."""
        excluded = self.non_client_facing_email_set()
        return [e for e in self.team_email_list() if e not in excluded]

    class Config:
        env_file = ".env"
        case_sensitive = False
        env_ignore_empty = True


settings = Settings()
