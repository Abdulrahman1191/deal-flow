# Portfolio Intelligence Plan

> Agreed: 2026-05-19 (after GP feedback)
> Owner: Abdulrahman Alhashim

## What the GP got right

We were about to train the AI on the wrong signal: "Raed funded X" ≢ "X was a good investment." Treating all past investments as YES labels would have:
- Reinforced theses we've already explored, blinding us to novel ones (the closed-loop problem)
- Encoded our past *bets* rather than our *learning* — including the bets that didn't work

The fix: track each company with **outcome** + **signals** + **our decision at the time**. Mine patterns across all of it. Let the AI surface — not enforce — the lessons.

## The 2×2 that drives this

```
                          OUTCOME (real-world)
                  ─────────────────────────────────────►
                  failed   stalled   alive   exited
                ┌─────────┬─────────┬────────┬─────────┐
   our   FUNDED │ mistake │ ?       │ ?      │ ✓ thesis│
   call         ├─────────┼─────────┼────────┼─────────┤
   PASSED       │ ✓ pass  │ ?       │ ?      │ MISSED  │
                └─────────┴─────────┴────────┴─────────┘
                                                ↑
                                  closed-loop breakers
                                  live HERE
```

The bottom-right quadrant (we passed, they exited) is the most valuable data we have. It's the only signal that says "your filter is too tight on this thesis."

## Data model

Three new tables. Independent from the `leads` / `assessment_cards` pipeline.

### `portfolio_companies` — one row per company we have an opinion on

```sql
CREATE TABLE portfolio_companies (
  id              UUID PRIMARY KEY,
  name            VARCHAR(255) NOT NULL,
  sector          VARCHAR(64),           -- free-text for now ("deep tech", "fintech", "marketplace")
  region          VARCHAR(64),
  founder_names   VARCHAR(255)[],
  website         VARCHAR(255),
  description     TEXT,
  our_decision    VARCHAR(16) NOT NULL,  -- FUNDED | PASSED | NOT_SEEN
  decision_at     DATE,                  -- when we made the call (approx OK)
  decision_rationale TEXT,               -- why funded / why passed
  invested_amount_usd BIGINT,            -- optional
  current_status  VARCHAR(16) NOT NULL,  -- denormalized latest outcome (see below)
  last_reviewed_at TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);
```

### `portfolio_outcomes` — time-series of state changes

```sql
CREATE TABLE portfolio_outcomes (
  id              UUID PRIMARY KEY,
  company_id      UUID REFERENCES portfolio_companies(id) ON DELETE CASCADE,
  status          VARCHAR(16) NOT NULL,  -- one of OUTCOME_STATES
  recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  current_valuation_usd BIGINT,
  last_round_stage VARCHAR(32),          -- pre-seed | seed | A | B | C | growth
  notes           TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);
```

### `portfolio_signals` — what we LEARNED from the company

```sql
CREATE TABLE portfolio_signals (
  id              UUID PRIMARY KEY,
  company_id      UUID REFERENCES portfolio_companies(id) ON DELETE CASCADE,
  signal_type     VARCHAR(32) NOT NULL,  -- controlled vocabulary, see below
  direction       VARCHAR(8) NOT NULL,   -- POSITIVE | NEGATIVE
  weight          SMALLINT NOT NULL CHECK (weight BETWEEN 1 AND 5),
  observed_at     DATE,
  note            TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);
```

## Controlled vocabularies

### Outcome states (7)

| State | When |
|---|---|
| `exited`    | Acquired or IPO |
| `growing`   | Raising at higher valuations, real traction milestones |
| `stalled`   | Alive but not scaling — flat ARR, no fundraise |
| `zombie`    | No growth, no death, no clarity. Going sideways for 18+ months |
| `failed`    | Officially dissolved, written down to zero, or founders moved on |
| `acqui_hire`| Small absorbing acquisition; founders moved to acquirer (ambiguous outcome) |
| `too_early` | Invested <18 months ago; data still maturing |

### Signal types (15)

Each signal is tagged with **direction** (POSITIVE/NEGATIVE) and **weight** (1-5).

| Type | Captures |
|---|---|
| `founder_execution` | Did they ship, hire, and run the company well |
| `founder_domain_fit` | Was their background actually relevant to the problem |
| `founder_team_chemistry` | Did cofounders stay aligned / avoid breakups |
| `market_timing` | Right thing, right time vs. too early/too late |
| `market_size` | Was the market big enough |
| `tech_moat_durability` | Did the moat hold up vs. get commodified |
| `distribution_capability` | Could they actually acquire customers |
| `capital_efficiency` | Burn rate vs. milestones achieved |
| `regulatory_environment` | Won/lost due to regulation |
| `unit_economics` | Did the unit math actually work |
| `pivot_required` | Did they have to change story to survive (and did pivot work?) |
| `macro_tailwind` | External factors (oil, war, FX, COVID) helped or hurt |
| `competitive_pressure` | Got beaten by someone else / dominated the field |
| `customer_concentration` | Over-dependent on a few customers or vice versa |
| `data_advantage` | Did proprietary data flywheel materialise |

### Our decision (3)

| Value | Means |
|---|---|
| `FUNDED` | We invested (regardless of outcome) |
| `PASSED` | We saw them and passed |
| `NOT_SEEN` | Added retroactively — we didn't see this deal at all, but it's instructive |

## Scope for v1 (this sprint)

| Bucket | Target count |
|---|---|
| FUNDED portfolio companies (with outcomes + signals) | 10-20 |
| PASSED deals we still remember and have an outcome view on | 5-10 |
| NOT_SEEN important misses | A few |

**~30 total companies** as the bootstrap. Cadence: quarterly review ritual to keep it fresh.

## UX (v1)

New top-level **Portfolio** tab in the navbar (owner-only, like Feedback). Three views:

1. **List view** — table: name | sector | our_decision | current_status | last_reviewed_at | signal count. Filterable, sortable.
2. **Detail view** — clicking a row expands: full description, decision rationale, all outcomes (time-series), all signals. Inline edit.
3. **Add modal** — quick-add form for a new company.

Not in v1:
- Pattern-mining LLM (Phase 2)
- Embedding-based novelty detection (Phase 3)
- Inline "similar companies" on the lead card (Phase 4)

## Phase plan

| Phase | What | Effort |
|---|---|---|
| **1. Foundation** (this sprint) | Schema + models + CRUD API + basic UI | ~3-4 h |
| **2. Pattern mining** | Monthly LLM job that reads all signals + outcomes and produces text "lessons learned"; lessons get injected into the assessment prompt | ~3-4 h |
| **3. Novelty detection** | pgvector embeddings of portfolio + each new lead; surface similarity score + flag out-of-distribution | ~4-6 h |
| **4. Inline integration** | On each new lead's card, show 2-3 closest historical portfolio matches with their outcomes | ~2-3 h |

## Open questions parked for now

- **Refresh cadence enforcement:** how do we remind ourselves to review quarterly? Cron + email + UI nag?
- **Public sources for outcomes:** can we auto-pull funding-round news for tracked companies to suggest status changes?
- **Confidence on signal weights:** is "weight 1-5" the right scale, or should we add a confidence number too?
- **Signal authorship:** for v1 only owner can edit; later allow GP collaboration with attribution?
- **Sector vocabulary:** free-text now, controlled later. When and how to migrate?

These are good problems to have once v1 is live. Park them.
