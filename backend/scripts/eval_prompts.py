"""
Prompt-evaluation harness.

Runs the current `assess_lead` pipeline against a labelled "golden" test set
and prints per-case results + aggregate metrics. Lets us measure whether a
prompt change actually improved bucket accuracy.

Usage:
  python scripts/eval_prompts.py                          # uses backend/tests/golden.json
  python scripts/eval_prompts.py --test-set path/to.json
  python scripts/eval_prompts.py --tag baseline           # adds a tag to the result file
  python scripts/eval_prompts.py --no-cache               # force fresh research even if cached

How it works:
  1. Loads test set: a list of {"lead_id": "...", "human_bucket": "YES|MAYBE|REJECT", "rationale": "..."}
  2. For each test case, runs `research_company(lead) + claude_agent.assess_lead()`
     against the live LLM. Output bucket compared to human_bucket.
  3. Computes strict 3-way accuracy + per-bucket precision + cost estimates.
  4. Persists results to `eval_runs/{timestamp}_{tag}.json` so we can compare
     run-over-run.

What "current" prompt means: whatever is in claude_agent.py at the moment.
Iterating: edit the prompt, run this script again, compare new accuracy to
the previous run's saved JSON.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.assessment import AssessmentCard
from app.models.lead import Lead
from app.services import claude_agent, research


BUCKETS = ("YES", "MAYBE", "REJECT")


def _color(text: str, ok: bool) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[32m{text}\033[0m" if ok else f"\033[31m{text}\033[0m"


async def _load_lead_and_card(db, lead_id: str):
    r = await db.execute(select(Lead).where(Lead.id == uuid.UUID(lead_id)))
    lead = r.scalar_one_or_none()
    if not lead:
        return None, None
    r2 = await db.execute(
        select(AssessmentCard).where(AssessmentCard.lead_id == lead.id)
        .order_by(AssessmentCard.created_at.desc()).limit(1)
    )
    card = r2.scalar_one_or_none()
    return lead, card


def _strict_accuracy(predictions: list[dict]) -> float:
    if not predictions:
        return 0.0
    correct = sum(1 for p in predictions if p["ai_bucket"] == p["human_bucket"])
    return round(100.0 * correct / len(predictions), 1)


def _per_bucket_precision(predictions: list[dict], bucket: str) -> tuple[int, int]:
    """How often, when the AI said `bucket`, did the human agree? (TP, total predictions for bucket)."""
    preds = [p for p in predictions if p["ai_bucket"] == bucket]
    total = len(preds)
    tp = sum(1 for p in preds if p["human_bucket"] == bucket)
    return tp, total


async def _run(test_set_path: Path, tag: str, use_cache: bool) -> dict:
    with open(test_set_path) as f:
        test_set = json.load(f)

    print(f"Loaded {len(test_set)} test case(s) from {test_set_path}")
    print(f"Tag: {tag}   Cache: {use_cache}")
    print("")

    predictions: list[dict] = []
    started = time.time()

    async with AsyncSessionLocal() as db:
        for case in test_set:
            lead_id = case["lead_id"]
            human_bucket = case["human_bucket"]
            lead, card = await _load_lead_and_card(db, lead_id)
            if not lead:
                print(f"  SKIP   {lead_id[:8]}…  (lead not found)")
                continue

            # Try cached research_data unless --no-cache; falls back to fresh search.
            research_data = card.research_data if (card and use_cache and card.research_data) else None
            if research_data is None:
                try:
                    research_data = research.research_company({
                        "company_name": lead.company_name,
                        "website": lead.website,
                        "description": lead.description,
                        "founder_names": lead.founder_names,
                        "region": lead.region,
                        "pitch_deck_text": lead.pitch_deck_text,
                    })
                except Exception as exc:
                    print(f"  ERR    {lead.company_name}: research failed: {exc!r}")
                    continue

            lead_data = {
                "company_name": lead.company_name,
                "website": lead.website,
                "description": lead.description,
                "stage": lead.stage,
                "region": lead.region,
                "founder_names": lead.founder_names,
                "linkedin_urls": lead.linkedin_urls,
                "company_linkedin_url": lead.company_linkedin_url,
                "pitch_deck_text": lead.pitch_deck_text,
            }

            t0 = time.time()
            try:
                result = claude_agent.assess_lead(lead_data, research_data)
            except Exception as exc:
                print(f"  ERR    {lead.company_name}: assess failed: {exc!r}")
                continue
            elapsed = round(time.time() - t0, 1)

            ai_bucket = (result.get("bucket") or "").upper()
            ai_conf = result.get("confidence_score")
            agree = ai_bucket == human_bucket
            mark = "✓" if agree else "✗"

            print(f"  {_color(mark, agree)}  {lead.company_name[:32]:32s}  AI: {ai_bucket:5s} (conf={ai_conf})  human: {human_bucket:5s}  [{elapsed}s]")

            predictions.append({
                "lead_id": lead_id,
                "company_name": lead.company_name,
                "ai_bucket": ai_bucket,
                "ai_confidence": ai_conf,
                "human_bucket": human_bucket,
                "agree": agree,
                "elapsed_s": elapsed,
            })

    total_elapsed = round(time.time() - started, 1)

    # Aggregate
    strict_acc = _strict_accuracy(predictions)
    metrics: dict = {
        "strict_accuracy_pct": strict_acc,
        "total_cases": len(predictions),
        "correct": sum(1 for p in predictions if p["agree"]),
        "wrong": sum(1 for p in predictions if not p["agree"]),
        "per_bucket_precision": {},
        "wall_clock_s": total_elapsed,
        "tag": tag,
        "test_set_path": str(test_set_path),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    for b in BUCKETS:
        tp, total = _per_bucket_precision(predictions, b)
        metrics["per_bucket_precision"][b] = {
            "tp": tp, "total": total,
            "precision_pct": round(100.0 * tp / total, 1) if total else None,
        }

    print("")
    print(f"Results (tag={tag!r}):")
    print(f"  Strict accuracy:   {strict_acc}%  ({metrics['correct']}/{metrics['total_cases']})")
    for b in BUCKETS:
        p = metrics["per_bucket_precision"][b]
        bar = f"{p['tp']}/{p['total']}" if p['total'] else "no predictions"
        print(f"  {b:6s} precision:  {p['precision_pct'] if p['precision_pct'] is not None else 'n/a':<5}%  ({bar})")
    print(f"  Wall clock:        {total_elapsed}s")

    # Persist
    out_dir = Path(__file__).resolve().parent.parent / "eval_runs"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"{ts}_{tag}.json"
    with open(out_path, "w") as f:
        json.dump({"metrics": metrics, "predictions": predictions}, f, indent=2)
    print(f"  Saved → {out_path}")

    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-set", default=str(Path(__file__).resolve().parent.parent / "tests" / "golden.json"))
    parser.add_argument("--tag", default="run", help="label saved to filename + metrics record")
    parser.add_argument("--no-cache", action="store_true", help="force fresh Tavily research even if cached on the assessment card")
    args = parser.parse_args()

    asyncio.run(_run(Path(args.test_set), args.tag, use_cache=not args.no_cache))


if __name__ == "__main__":
    main()
