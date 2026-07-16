"""
Guards on the draft-writing prompt text: every lead is inbound, so drafts must
never read as cold outbound. See issue #28 — the two prompt copies
(ASSESS_USER_TEMPLATE and DRAFT_REGEN_SYSTEM/DRAFT_REGEN_USER_TEMPLATE) drift
easily, so these checks run against both.
"""
from app.services.claude_agent import (
    ASSESS_USER_TEMPLATE,
    DRAFT_REGEN_SYSTEM,
    DRAFT_REGEN_USER_TEMPLATE,
)

BANNED_PHRASES = [
    "I came across",
    "came across your company",
    "caught my eye",
    "caught our attention",
    "I've been following",
    "we discovered",
    "reaching out because we noticed",
]

# The two prompt "copies" called out in the issue as needing to stay consistent.
ASSESS_DRAFT_BLOCK = ASSESS_USER_TEMPLATE
REGEN_BLOCK = DRAFT_REGEN_SYSTEM + DRAFT_REGEN_USER_TEMPLATE


def test_both_prompts_state_inbound_context():
    for block in (ASSESS_DRAFT_BLOCK, REGEN_BLOCK):
        assert "inbound" in block.lower() or "applying" in block.lower()
        assert "applied" in block.lower() or "applying" in block.lower()


def test_both_prompts_ban_outbound_phrasing():
    for block in (ASSESS_DRAFT_BLOCK, REGEN_BLOCK):
        for phrase in BANNED_PHRASES:
            assert phrase in block, f"expected banned-phrase instruction {phrase!r} in prompt"


def test_both_prompts_ban_hype_adjectives():
    for block in (ASSESS_DRAFT_BLOCK, REGEN_BLOCK):
        assert "thrilled" in block.lower()
        assert "impressed by your incredible" in block.lower()


def test_both_prompts_enforce_word_limits():
    for block in (ASSESS_DRAFT_BLOCK, REGEN_BLOCK):
        assert "50 WORDS" in block
        assert "70 WORDS" in block


def test_both_prompts_include_calendly_and_signoff():
    for block in (ASSESS_DRAFT_BLOCK, REGEN_BLOCK):
        assert "calendly.com/abdulrahman-raed/30min" in block
        assert "{associate_name}, Raed Ventures" in block


def test_both_prompts_never_invent_founder_title():
    for block in (ASSESS_DRAFT_BLOCK, REGEN_BLOCK):
        assert "no assumed title" in block.lower() or "any title unless verified" in block.lower()


def test_maybe_bucket_nulls_all_draft_fields_in_both_prompts():
    for block in (ASSESS_DRAFT_BLOCK, REGEN_BLOCK):
        assert "draft_type, draft_subject, draft_body" in block or (
            "draft_type" in block and "draft_subject" in block and "draft_body" in block
        )
