"""
One-shot upload of local pitch deck PDFs → S3 for the platform migration.

Usage:
  python scripts/upload_pitch_decks_to_s3.py --source /opt/raed/pitch-decks

Iterates every *.pdf in --source, uploads to s3://${S3_PITCH_DECK_BUCKET}/decks/<filename>
(skipping files already present unless --force is set). Prints a summary
including how many bytes uploaded and any failures.

The IAM user used here needs s3:PutObject + s3:ListBucket on the target bucket.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services import s3_pitch_decks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="Local directory of .pdf files")
    parser.add_argument("--force", action="store_true", help="Re-upload files that already exist in S3")
    args = parser.parse_args()

    if not s3_pitch_decks.is_configured():
        raise SystemExit("S3_PITCH_DECK_BUCKET env var must be set")

    src = Path(args.source)
    if not src.is_dir():
        raise SystemExit(f"--source not a directory: {src}")

    pdfs = sorted(p for p in src.iterdir() if p.suffix.lower() == ".pdf")
    if not pdfs:
        raise SystemExit(f"No .pdf files in {src}")

    bucket = settings.s3_pitch_deck_bucket
    client = s3_pitch_decks._client()

    uploaded = skipped = failed = 0
    total_bytes = 0

    print(f"Uploading {len(pdfs)} PDFs to s3://{bucket}/decks/")
    for path in pdfs:
        key = s3_pitch_decks.key_for_filename(path.name)
        if not args.force and s3_pitch_decks.object_exists(path.name):
            skipped += 1
            print(f"  skip  {path.name} (already in S3)")
            continue
        try:
            client.upload_file(
                Filename=str(path),
                Bucket=bucket,
                Key=key,
                ExtraArgs={"ContentType": "application/pdf"},
            )
            sz = path.stat().st_size
            total_bytes += sz
            uploaded += 1
            print(f"  ok    {path.name} ({sz / 1024:.0f} KB)")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {path.name}: {exc!r}")

    mb = total_bytes / 1024 / 1024
    print(f"\nDone. {uploaded} uploaded ({mb:.1f} MB), {skipped} skipped, {failed} failed.")


if __name__ == "__main__":
    main()
