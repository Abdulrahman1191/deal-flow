"""
Data-quality report: missing pitch decks + thin-data ("half-baked") REJECT
cards, per owner_email.

Read-only. Surveys `leads` (+ their latest `assessment_cards` row) and
reports, overall and broken down by owner_email:

  1. Leads missing a pitch deck: pitch_deck_text is null/empty AND
     pitch_deck_drive_id is null/empty.
  2. REJECT cards that look thin-data-driven rather than a genuine poor fit
     ("half-baked" cards) -- see the tunable thresholds below for the exact
     criteria. The assessment engine is designed to never reject on missing
     data alone (it should score mid-range + log a data_gap instead), so a
     REJECT with low confidence + data gaps + no deck is exactly the case
     worth a human's review.

Usage (from backend/):
  python scripts/data_quality_report.py
  python scripts/data_quality_report.py --owner someone@raed.vc
  python scripts/data_quality_report.py --markdown /tmp/report.md --csv /tmp/report.csv
  python scripts/data_quality_report.py --all      # include archived leads

Reads DATABASE_URL from env. Strictly read-only: no writes to the DB, no
reassessment, no Copper/Drive calls.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.database import AsyncSessionLocal
from app.models.lead import Lead
from app.services.csv_export import effective_bucket

# --- Tunable thresholds ------------------------------------------------------
# A REJECT card is flagged "half-baked" (thin-data driven, not a genuine poor
# fit) when its effective bucket (user_override if set, else bucket) is
# REJECT_BUCKET AND at least one of these thin-evidence signals holds:
#   - confidence_score < CONFIDENCE_THRESHOLD
#   - data_gaps is non-empty
#   - the lead has no pitch deck
REJECT_BUCKET = "REJECT"
CONFIDENCE_THRESHOLD = 50

UNASSIGNED_OWNER = "(unassigned)"


# --- Classifier helpers (pure, unit-tested, no DB access) -------------------

def has_pitch_deck(lead) -> bool:
    """A lead 'has a deck' if either the extracted text or the Drive file id is present."""
    return bool(lead.pitch_deck_text) or bool(lead.pitch_deck_drive_id)


def is_missing_pitch_deck(lead) -> bool:
    return not has_pitch_deck(lead)


def is_half_baked_reject(lead, card) -> bool:
    """True if `card`'s effective bucket is REJECT_BUCKET and the reject looks
    thin-data-driven rather than a genuine poor fit -- see thresholds above."""
    if card is None:
        return False
    if effective_bucket(card) != REJECT_BUCKET:
        return False
    return (
        (card.confidence_score is not None and card.confidence_score < CONFIDENCE_THRESHOLD)
        or bool(card.data_gaps)
        or is_missing_pitch_deck(lead)
    )


# --- Report data model -------------------------------------------------------

@dataclass
class OwnerReport:
    owner: str
    total: int = 0
    missing_deck: list = field(default_factory=list)  # lead names
    half_baked: list = field(default_factory=list)  # dicts: name/effective_bucket/confidence_score/has_deck/data_gaps_count

    @property
    def missing_deck_count(self) -> int:
        return len(self.missing_deck)

    @property
    def missing_deck_pct(self) -> float:
        return _pct(self.missing_deck_count, self.total)

    @property
    def half_baked_count(self) -> int:
        return len(self.half_baked)

    @property
    def half_baked_pct(self) -> float:
        return _pct(self.half_baked_count, self.total)


def _pct(n: int, total: int) -> float:
    return round(100.0 * n / total, 1) if total else 0.0


def build_report(rows: list) -> tuple[OwnerReport, dict]:
    """rows: list of (lead, card) tuples. Returns (overall, by_owner)."""
    overall = OwnerReport(owner="(overall)")
    by_owner: dict = {}

    for lead, card in rows:
        owner = lead.owner_email or UNASSIGNED_OWNER
        owner_report = by_owner.setdefault(owner, OwnerReport(owner=owner))

        for r in (overall, owner_report):
            r.total += 1
            if is_missing_pitch_deck(lead):
                r.missing_deck.append(lead.company_name)
            if is_half_baked_reject(lead, card):
                r.half_baked.append({
                    "name": lead.company_name,
                    "effective_bucket": effective_bucket(card),
                    "confidence_score": card.confidence_score,
                    "has_deck": has_pitch_deck(lead),
                    "data_gaps_count": len(card.data_gaps or []),
                })

    return overall, by_owner


# --- DB access ----------------------------------------------------------------

async def fetch_rows(db, owner: Optional[str], include_archived: bool) -> list:
    query = select(Lead).options(joinedload(Lead.assessment))
    if not include_archived:
        query = query.where(Lead.status != "archived")
    if owner:
        query = query.where(Lead.owner_email == owner)
    result = await db.execute(query)
    leads = result.unique().scalars().all()
    return [(lead, lead.assessment) for lead in leads]


async def _fetch_and_build(owner: Optional[str], include_archived: bool) -> tuple:
    async with AsyncSessionLocal() as db:
        rows = await fetch_rows(db, owner, include_archived)
    return build_report(rows)


# --- Rendering ------------------------------------------------------------

def report_header(owner_filter: Optional[str], include_archived: bool) -> str:
    scope = "all leads (including archived)" if include_archived else "active leads only (status != archived)"
    if owner_filter:
        scope += f", owner={owner_filter}"
    return "\n".join([
        "Data-quality report: missing pitch decks + half-baked REJECT cards",
        f"Scope: {scope}",
        "Criteria:",
        "  Missing pitch deck   -- pitch_deck_text is null/empty AND pitch_deck_drive_id is null/empty.",
        f"  Half-baked REJECT    -- effective bucket (user_override if set, else bucket) == {REJECT_BUCKET!r} AND"
        f" (confidence_score < {CONFIDENCE_THRESHOLD} OR data_gaps non-empty OR no pitch deck).",
    ])


def _owner_lines(r: OwnerReport) -> list:
    lines = [
        f"Total leads: {r.total}",
        f"Missing pitch deck: {r.missing_deck_count} ({r.missing_deck_pct}%)",
    ]
    for name in r.missing_deck:
        lines.append(f"    - {name}")
    lines.append(f"Half-baked REJECT: {r.half_baked_count} ({r.half_baked_pct}%)")
    for item in r.half_baked:
        lines.append(
            f"    - {item['name']}  bucket={item['effective_bucket']}"
            f"  confidence={item['confidence_score']}  has_deck={item['has_deck']}"
            f"  data_gaps={item['data_gaps_count']}"
        )
    return lines


def render_stdout(overall: OwnerReport, by_owner: dict, owner_filter: Optional[str], include_archived: bool) -> str:
    lines = [report_header(owner_filter, include_archived), "", "=== Overall ===", *_owner_lines(overall), ""]
    for owner in sorted(by_owner):
        lines.append(f"=== Owner: {owner} ===")
        lines.extend(_owner_lines(by_owner[owner]))
        lines.append("")
    return "\n".join(lines)


def render_markdown(overall: OwnerReport, by_owner: dict, owner_filter: Optional[str], include_archived: bool) -> str:
    lines = ["# Data-quality report: missing pitch decks + half-baked REJECT cards", ""]
    lines.append(report_header(owner_filter, include_archived).replace("\n", "\n\n"))
    lines.append("")

    def section(r: OwnerReport, title: str) -> list:
        s = [f"## {title}", "", f"- Total leads: {r.total}",
             f"- Missing pitch deck: {r.missing_deck_count} ({r.missing_deck_pct}%)"]
        s.extend(f"  - {name}" for name in r.missing_deck)
        s.append(f"- Half-baked REJECT: {r.half_baked_count} ({r.half_baked_pct}%)")
        s.extend(
            f"  - {item['name']} -- bucket={item['effective_bucket']}, "
            f"confidence={item['confidence_score']}, has_deck={item['has_deck']}, "
            f"data_gaps={item['data_gaps_count']}"
            for item in r.half_baked
        )
        s.append("")
        return s

    lines.extend(section(overall, "Overall"))
    for owner in sorted(by_owner):
        lines.extend(section(by_owner[owner], f"Owner: {owner}"))
    return "\n".join(lines)


CSV_SUMMARY_HEADERS = ["owner", "total_leads", "missing_deck_count", "missing_deck_pct",
                        "half_baked_count", "half_baked_pct"]
CSV_MISSING_DECK_HEADERS = ["owner", "lead_name"]
CSV_HALF_BAKED_HEADERS = ["owner", "lead_name", "effective_bucket", "confidence_score",
                           "has_deck", "data_gaps_count"]


def render_csv(overall: OwnerReport, by_owner: dict) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(["# summary"])
    writer.writerow(CSV_SUMMARY_HEADERS)
    for r in [overall] + [by_owner[o] for o in sorted(by_owner)]:
        writer.writerow([r.owner, r.total, r.missing_deck_count, r.missing_deck_pct,
                          r.half_baked_count, r.half_baked_pct])

    writer.writerow([])
    writer.writerow(["# missing_pitch_decks"])
    writer.writerow(CSV_MISSING_DECK_HEADERS)
    for owner in sorted(by_owner):
        for name in by_owner[owner].missing_deck:
            writer.writerow([owner, name])

    writer.writerow([])
    writer.writerow(["# half_baked_rejects"])
    writer.writerow(CSV_HALF_BAKED_HEADERS)
    for owner in sorted(by_owner):
        for item in by_owner[owner].half_baked:
            writer.writerow([owner, item["name"], item["effective_bucket"], item["confidence_score"],
                              item["has_deck"], item["data_gaps_count"]])

    return buf.getvalue()


# --- CLI ---------------------------------------------------------------------

def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", help="Filter to a single owner_email.")
    parser.add_argument("--markdown", help="Write the report as markdown to this path.")
    parser.add_argument("--csv", help="Write the report as CSV to this path.")
    parser.add_argument("--all", action="store_true", help="Include archived leads (default: active leads only).")
    args = parser.parse_args(argv)

    overall, by_owner = asyncio.run(_fetch_and_build(args.owner, args.all))

    print(render_stdout(overall, by_owner, args.owner, args.all))

    if args.markdown:
        Path(args.markdown).write_text(render_markdown(overall, by_owner, args.owner, args.all))
        print(f"\nMarkdown report written to {args.markdown}")
    if args.csv:
        Path(args.csv).write_text(render_csv(overall, by_owner))
        print(f"CSV report written to {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
