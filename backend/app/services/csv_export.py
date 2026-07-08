from __future__ import annotations
import csv
import io
from typing import Iterable, Optional

LEADS_CSV_HEADERS = ["Company Name", "Bucket", "Confidence Score", "Created Date"]


def effective_bucket(assessment) -> Optional[str]:
    """The bucket the user actually sees: a manual override wins over the
    AI's original bucket. Mirrors the kanban grouping logic in LeadsPage."""
    if not assessment:
        return None
    return assessment.user_override or assessment.bucket


def build_leads_csv(rows: Iterable[dict]) -> str:
    """Renders lead rows to CSV text. Each row is a dict with keys
    company_name, bucket, confidence_score, created_date. Always emits the
    header row, even when `rows` is empty."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(LEADS_CSV_HEADERS)
    for row in rows:
        writer.writerow([
            row["company_name"],
            row["bucket"],
            row["confidence_score"],
            row["created_date"],
        ])
    return buf.getvalue()
