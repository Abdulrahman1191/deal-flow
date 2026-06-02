"""
S3-backed pitch deck storage.

On the platform there is no persistent local volume, so the 427 PDFs that
used to live at `/opt/raed/pitch-decks/` move to an S3 bucket. Two access
patterns:

  - Read:  presigned URL valid for 5 minutes. The /leads/{id}/pitch-deck
           endpoint returns a 307 redirect to the presigned URL. Browsers
           follow the redirect, the S3 GET completes directly between the
           client and AWS — no bytes flow through our backend.

  - Write: a one-shot bulk-upload script (scripts/upload_pitch_decks_to_s3.py)
           syncs the local Lightsail directory to S3. After cutover, the
           Cowork-driven ingestion writes directly to S3 (out of scope for
           this migration).

Bucket is configured via S3_PITCH_DECK_BUCKET. AWS creds via
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (the same dedicated IAM user
that handles backups already has S3 permissions on this bucket).
"""
from __future__ import annotations
from typing import Optional

import boto3
from botocore.config import Config

from app.config import settings


_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
            region_name=settings.aws_region or None,
            config=Config(signature_version="s3v4"),
        )
    return _s3


def is_configured() -> bool:
    return bool(settings.s3_pitch_deck_bucket)


def key_for_filename(filename: str) -> str:
    """S3 key convention. Filenames are unique-by-company so the flat layout
    is fine. Strip any leading slashes defensively."""
    return f"decks/{filename.lstrip('/')}"


def presigned_get_url(filename: str, ttl_seconds: int = 300) -> Optional[str]:
    """Returns a presigned URL the browser can fetch directly, or None if the
    S3 bucket isn't configured (defensive — should never happen in prod)."""
    if not is_configured():
        return None
    return _client().generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": settings.s3_pitch_deck_bucket,
            "Key": key_for_filename(filename),
        },
        ExpiresIn=ttl_seconds,
    )


def object_exists(filename: str) -> bool:
    """Cheap HEAD probe — used by the endpoint to differentiate 'no deck on
    record' (404 from DB) vs 'we said there's a deck but the file is missing
    from S3' (also 404, but a different message for debuggability)."""
    if not is_configured():
        return False
    try:
        _client().head_object(
            Bucket=settings.s3_pitch_deck_bucket,
            Key=key_for_filename(filename),
        )
        return True
    except Exception:
        return False
