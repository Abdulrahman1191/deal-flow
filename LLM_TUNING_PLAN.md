# LLM Tuning Plan — Raed Ventures AI Deal Flow

> Agreed: 2026-05-18
> Owner: Abdulrahman Alhashim
> North star: **Improve AI bucket accuracy over the next quarter using a measured, data-driven loop.**

## Constraints we agreed on

| Decision | Value |
|---|---|
| Optimization target | Strict 3-way bucket accuracy (AI bucket == human bucket) |
| AI autonomy posture | AI proposes, human always decides |
| Timeframe | Quarterly (patient — build the right foundation now) |
| Test set bootstrap | Claude picks 8 leads spanning the spectrum, human reviews labels |

## Levels of intervention — where we are and where we're not going

| Level | What | Today | Where we'll be in 3mo |
|---|---|---|---|
| L1 — Prompt engineering | Rubrics, guardrails, instructions | ✅ Active (last sprint) | Continuous iteration |
| L2 — Few-shot prompting | Include override examples in prompt | ❌ Blocked — 0 overrides today | Active once ≥10 overrides captured |
| L3 — RAG over past decisions | Retrieve similar past leads at inference | ❌ Premature | Plan in motion at 50+ decisions |
| L4 — Fine-tune model weights | Train custom checkpoint | ❌ Wrong scale, ignore | Still ignored |

## The metric

**Strict 3-way accuracy = (AI bucket == human bucket) / total leads in test set.**

Secondary metrics tracked alongside:
- **Override rate** (production): % of leads the user flipped. Falling = improving.
- **YES precision**: when AI says YES, % human agrees. Critical (we email these).
- **REJECT precision**: when AI says REJECT, % human agrees. Critical (we drop these).
- **MAYBE→YES conversion**: of MAYBEs, what % the user eventually accepts.

## Architecture

### A. `assessment_overrides` table

```sql
CREATE TABLE assessment_overrides (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id         UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  assessment_id   UUID NOT NULL REFERENCES assessment_cards(id) ON DELETE CASCADE,
  ai_bucket       VARCHAR(8) NOT NULL,
  ai_confidence   INTEGER,
  ai_summary      TEXT,
  ai_breakdown    JSONB,
  human_bucket    VARCHAR(8) NOT NULL,
  trigger         VARCHAR(16) NOT NULL,   -- override | approve | skip | send
  research_snap   JSONB,                  -- raw Tavily research at time of AI call
  deck_excerpt    TEXT,                   -- first 12k chars of pitch deck
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_overrides_created_at ON assessment_overrides(created_at);
CREATE INDEX ix_overrides_disagreement ON assessment_overrides((ai_bucket != human_bucket));
```

Auto-populated on three triggers:
- **override** — user clicks YES/MAYBE/REJECT chip → snapshot pre-override state vs new bucket
- **approve** — user clicks Approve Email/Meeting Request → implicit confirmation of current bucket
- **skip** — user clicks Skip ⤬ → implicit confirmation of REJECT (when AI didn't say REJECT)

The research snapshot is the key: 6 months from now we need to know **what the AI saw** when it made the call, not just what it said.

### B. Evaluation harness (`backend/scripts/eval_prompts.py`)

CLI tool. Reads a JSON golden test set, runs the current `assess_lead` pipeline against each lead, prints per-case results + aggregate metrics.

```
$ python scripts/eval_prompts.py --test-set tests/golden.json [--prompt-version VAR]

Loaded 8 test cases.
Running prompt 'current' against test set...
  Hadawi              AI: YES   | human: YES    ✓
  Drb Station         AI: MAYBE | human: REJECT ✗
  ...

Results (prompt='current'):
  Strict accuracy:   75% (6/8)
  YES precision:    100% (2/2)
  REJECT precision:  50% (1/2)
  Avg latency:      8.2s
  Total cost:       ≈ $0.04
```

### C. The golden test set

`backend/tests/golden.json`:
```json
[
  {"lead_id": "...", "human_bucket": "YES",    "rationale": "Real deep tech in MENA"},
  {"lead_id": "...", "human_bucket": "REJECT", "rationale": "Pure marketplace"},
  ...
]
```

Bootstrap: Claude picks 8 leads from current 40, proposes labels. Human reviews and corrects in ~10 min. Test set grows organically as override data accumulates.

## Workflow

```
You override a lead in the UI
    ↓
backend snapshots (input, ai_call, human_call) → assessment_overrides
    ↓
Weekly:  eyeball recent overrides, promote interesting cases into golden test set
    ↓
When proposing a prompt change:
    1. Edit ASSESS_SYSTEM / ASSESS_USER_TEMPLATE on a branch
    2. Run `eval_prompts.py --prompt-version proposed`
    3. Compare strict_accuracy vs current
    4. If better → ship. If worse → discard.
```

## Sprint plan

| Sprint | Deliverable | Effort | Status |
|---|---|---|---|
| **1** | `assessment_overrides` table (migration) + auto-capture on the 3 triggers + read-only GET endpoint | ~1.5h | 🟡 Starting now |
| **2** | `eval_prompts.py` harness + bootstrap test set (Claude proposes 8 labels, human reviews) | ~1.5h | Pending |
| **3** | First measured prompt experiment: try removing the safety override, compare accuracy | ~1h | Pending |
| **Ongoing** | Run harness on every prompt change. Track accuracy over time. Revisit Sprint 4 (Level 2 few-shot) once ≥10 overrides captured. | rolling | — |

## What we explicitly do NOT do

- Few-shot prompting (Level 2) — until ≥10 captured overrides exist
- pgvector / RAG (Level 3) — until ≥50 decisions exist
- Fine-tune model weights (Level 4) — not on the horizon at this scale
- Auto-deploy prompt changes — human in the loop on every prompt diff
