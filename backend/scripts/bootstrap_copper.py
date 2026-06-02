"""
One-shot bootstrap: look up Copper IDs we need for sync (user, statuses, pipeline, stage)
and print the lines to append to backend/.env.

Run from backend/ directory: `python scripts/bootstrap_copper.py`
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.copper_service import (
    lookup_user_id,
    lookup_open_status_id,
    lookup_status_id_by_name,
    lookup_pipeline_and_first_stage,
)


def main() -> None:
    print(f"Looking up Copper user_id for {settings.copper_user_email}...")
    user_id = lookup_user_id(settings.copper_user_email)
    print(f"  user_id = {user_id}")

    print("Looking up default ('Open' / 'New') lead status_id...")
    open_status_id = lookup_open_status_id()
    print(f"  open_status_id = {open_status_id}")

    print("Looking up 'Unqualified' lead status_id...")
    unqualified_status_id = lookup_status_id_by_name("Unqualified")
    print(f"  unqualified_status_id = {unqualified_status_id}")

    print("Looking up 'Fund 3 (2024 - Present)' pipeline + first stage...")
    pipeline_id, stage_id = lookup_pipeline_and_first_stage("Fund 3 (2024 - Present)")
    print(f"  pipeline_id = {pipeline_id}")
    print(f"  first_stage_id = {stage_id}")

    print("\nAppend these lines to backend/.env:")
    print(f"COPPER_USER_ID={user_id}")
    print(f"COPPER_OPEN_STATUS_ID={open_status_id}")
    print(f"COPPER_UNQUALIFIED_STATUS_ID={unqualified_status_id}")
    print(f"COPPER_PIPELINE_ID={pipeline_id}")
    print(f"COPPER_PIPELINE_STAGE_ID={stage_id}")


if __name__ == "__main__":
    main()
