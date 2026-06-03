# Raed Portfolio Retrospective v2 — LLM Reading Guide

## Purpose of this file

This Excel file (`raed_portfolio_v2.xlsx`) is a structured retrospective of every active or completed investment by Raed Ventures, designed to train a recommendation engine for evaluating future deals.

Each row is one company. Each row contains: (a) what we believed at the moment we invested (forward-looking), (b) what actually happened after we invested (backward-looking), and (c) a synthesis layer with structured labels suitable as training targets.

The intended use is twofold:

1. **As training data**: each row is a complete (thesis → outcome → labels) example. Use the structured columns as classification targets and the prose columns as reasoning evidence.

2. **As a runtime knowledge base**: when evaluating a new opportunity, retrieve the most similar historical rows (by sector, stage, geography, founder type, thesis pattern), apply the mental models named in those rows' Lessons column, and use the disagreement-flagged rows to identify failure modes the new deal might replicate.

---

## The core discipline (read this before reading any row)

The retrospective is built on one foundational separation: **decision quality is separate from outcome**.

A bet can be a STRONG YES with bad outcome (good process, bad luck), or a LUCKY WIN where the thesis was thin and the company succeeded for reasons we didn't underwrite. Mixing the two destroys the training signal.

Three rules govern the data:

1. **Forward-looking columns (blue) use only signals knowable at investment time.** Founder pedigree at entry, market thesis stated in the IC memo, regulatory positioning observable then, traction at investment date, comparable benchmarks available at that time. Anything that emerged later is excluded from these columns.

2. **Backward-looking columns (green) use post-investment observations.** Performance trajectory, follow-on rounds, market shifts that occurred after we wrote the check, founder execution under stress, competitive responses we couldn't have predicted.

3. **The synthesis columns (purple) combine both, but maintain the separation.** `Performance Status` lives in reality today. `Hindsight Verdict` evaluates only whether the at-investment signals justified the bet. `Lessons` extract the transferable insight.

The highest-value training rows are flagged in column AM (`Rationale↔Signals Disagreement?`) where forward-looking belief and backward-looking observation point in different directions. Those are the rows where the model learns what we get *systematically wrong*, not just what we get right.

---

## File structure

- **Sheet: "Raed Portfolio"** — 42 rows × 39 columns. Each row = one investment.
- **Sheet: "_lists"** — reference sheet containing the controlled vocabularies (signal types, directions, weights, outcomes, mental models). Use this to validate enum values.

The header row is color-coded:
- **Blue (columns A–H)** = forward-looking (at investment)
- **Green (columns I–AG)** = backward-looking (post-investment)
- **Purple (columns AH–AM)** = synthesis

---

## Column-by-column definitions

### Forward-looking (at investment)

**A. Company** — Common name used internally.

**B. Founders** — Names + brief credentials at investment time. Where credentials were notably weak (no prior industry experience), this is captured explicitly.

**C. Year** — Year of initial Raed investment (not most recent round; not company founding year).

**D. Description** — One-line product + sector + geography. Helps the model retrieve by similarity.

**E. Original Thesis** — A single falsifiable sentence in the format: *"We back [Company] because [mechanism of value creation], in [market] where [why now], defended by [unfair advantage] — and we're wrong if [killer condition]."* This is the Muneer-skill format for thesis articulation. If it doesn't fit one sentence, the thesis was muddled. The "wrong if" clause is the kill criterion — the observable failure mode that would invalidate the bet.

**F. Why Now** — What changed in the 12–24 months before investment that made the bet possible at this moment. Regulatory shift, infra maturity, behavior change, capital flow, talent unlock. Must be specific to this deal — "AI is hot" is not a why-now.

**G. Decision Rationale** — 1–2 sentences capturing the IC-time belief about founder + investment terms + key proof points. This preserves the original portfolio template field. Source is the IC memo or DD report from Drive where available.

**H. Thesis Tags** — Multi-enum from {FOUNDER QUALITY, UNFAIR-ADVANTAGE, MARKET-TAILWIND, TIMING BET, PORTFOLIO-FIT}. Which pillar(s) the bet primarily rested on. Tagged at investment time, not retrospectively.

### Backward-looking (post-investment)

**I. What Happened After** — Prose summary of post-investment trajectory. Includes Visible metrics where reported, public funding events, key personnel changes, strategic pivots, market context shifts. Excludes information that was available at investment.

**J–AC. 5 Signal slots × 4 fields each** — The structured backward-looking observations.

Each slot has four fields:
- **signal_N_type** (enum, see Signal Taxonomy below)
- **signal_N_direction** (POSITIVE / NEGATIVE) — Did this signal contribute to success (POSITIVE) or to failure/friction (NEGATIVE). Direction is about whether the signal helped or hurt, not about the criterion's intrinsic valence.
- **signal_N_weight** (1–5) — 1 = minor factor, 5 = decisive, this alone shaped the outcome
- **signal_N_note** — Free text explaining the observation

Signal slots are not ranked — slot 1 is not "most important." They are 5 parallel observations. A row may have fewer than 5 slots populated.

**AD. Mental Models Applied** — Pipe-separated list of named frames from the Lemkin/O'Driscoll library plus Raed's 3 filters. These are the lenses through which the gap between thesis and reality is being interpreted. See Mental Models Library section.

**AE. Founder Obsession outcome** — Did the founder-quality bet validate? Enum: {VALIDATED, PARTIAL, FAILED, TOO EARLY}. Maps to Raed's first filter.

**AF. Market Scale outcome** — Did the market materialize at the size required for the exit math? Same enum.

**AG. Unfair Advantage outcome** — Did the moat hold or compound? Same enum. NOTE: If the moat that ended up mattering is different from the one underwritten at investment, this is PARTIAL — and the row gets flagged as a disagreement (column AM).

**AH. Why-Now still valid?** — Enum: {YES, PARTIAL, NO}. Is the regulatory/behavioral/macro shift that justified the timing of the investment still in effect?

### Synthesis

**AI. Performance Status** — Enum: {OUTPERFORMING, ON-TRACK, UNDERPERFORMING, WRITE-OFF, EXITED, TOO EARLY, UNKNOWN}. Reality today. Hindsight is the point here.

**AJ. Hindsight Verdict** — Enum: {STRONG YES, YES, MIXED, NO, TOO EARLY}. Was the decision sound based ONLY on at-investment signals? Decision quality, not outcome. A company can be UNDERPERFORMING + STRONG YES (good process, bad luck) or OUTPERFORMING + MIXED (lucky win on weak thesis). The separation is the entire point.

**AK. Current Status** — Enum from the original template: {growing, stalled, zombie, failed, exited, acqui_hire, too_early}. Less analytical than Performance Status; more like a state label.

**AL. Lessons** — Prose synthesis. Numbered LESSON 1, 2, 3... Each lesson is a transferable insight in the form: "When [pattern X is observed], [implication Y for future decisions]." These are the artifacts most useful for retrieval-augmented evaluation of new opportunities.

**AM. Rationale↔Signals Disagreement?** — Boolean {TRUE, FALSE}. TRUE means forward-looking belief and backward-looking observation point in materially different directions. The 15 TRUE rows are the highest-value training data — they teach the model what we systematically mis-underwrite, not just what we got right.

---

## Signal taxonomy (15 controlled values)

These are the only valid values for signal_N_type columns. Each captures one dimension of how a venture's outcome can be observed.

- **founder_execution** — Did they ship, hire, scale, and run the company well.
- **founder_domain_fit** — Was the founder's background actually relevant to the problem space.
- **founder_team_chemistry** — Did co-founders stay aligned; did the team avoid breakups.
- **market_timing** — Right thing, right time vs. too early or too late.
- **market_size** — Was the TAM/SAM big enough to support venture-scale outcomes.
- **tech_moat_durability** — Did the technical moat hold or get commoditized.
- **distribution_capability** — Could they actually acquire customers at scale.
- **capital_efficiency** — Burn rate vs. milestones achieved.
- **regulatory_environment** — Won or lost due to regulation.
- **unit_economics** — Did the unit-level math actually work.
- **pivot_required** — Did the company have to change story to survive — and did it work.
- **macro_tailwind** — External factors (oil, war, FX, COVID) helped or hurt.
- **competitive_pressure** — Got beaten by someone else / dominated the field.
- **customer_concentration** — Over-dependent on a few customers, or appropriately diversified.
- **data_advantage** — Did the proprietary data flywheel actually materialize.

Use these consistently. If a new deal needs a signal type not on this list, the categorization is wrong — find the closest fit on this list.

---

## Mental Models library

The Mental Models Applied column references frames from the Lemkin/O'Driscoll library plus Raed's 3 filters. The full library is documented in the Muneer opportunity-assessment skill. Brief reference:

**Valuation & Multiples**
- A1. Bedrock Multiple — Every valuation is a spread above the median public-comp multiple for the asset class.
- A2. Multiples by Growth Tier — Multiples should map to growth tier (e.g., >60% growth → 20-30x ARR for SaaS).
- A3. Growth Decay Rule — Each year's growth is roughly 85% of the prior year's.
- A4. Entry Price vs TAM — Entry price matters when TAM is uncertain; winning is the only thing that matters when TAM is huge.
- A5. Mark-to-Reality — 2021 marks need to validate via continued growth or they become embarrassing markdowns.
- A6. Markets Price the Delta — Markets price the surprise, not the news.

**Growth Quality**
- B1. Real Growth vs Fake Growth — Strip price increases and ARPU-only growth from headline growth.
- B2. NRR > GRR (Maiming Test) — High GRR with falling NRR means existing customers are shrinking, not churning. Business is being maimed.
- B3. 1-in-3 Reacceleration Base Rate — Only ~1 in 3 companies reaccelerates after deceleration.
- B4. Pulled-Forward Demand (COVID Mistake) — Frenzy-period growth as a steady-state assumption produces catastrophic forecasts.
- B5. TAM Exhaustion (1% Rule) — At $100M ARR you should have ≤1% market share; more means TAM headwall is near.

**Capital Discipline & Burn**
- C1. Burn Multiple — Net Burn / Net New ARR. Target <2x at Series A, <1x at growth.
- C2. Hubristic Financing — The round that goes too high before the music stops.
- C3. Grind Exit Trap — At $200M revenue × 5x = $1B exit; for a $150M fund needing 3x, a 5% stake = $50M ≠ fund-mover.
- C4. Spend Like #1 Trap — Spending like the category leader when you're #3 burns the cash that would let you survive.

**Defensibility**
- D1. Fortnite Effect — Defensible surface area shrinks every year as adjacent tech nibbles at the edges.
- D2. Maiming Not Killing — Disruption rarely kills incumbents fast; it shrinks them slowly.
- D3. Integrations as Survival — Number of integrations is the #1 predictor of survival in commoditized markets.
- D4. Category Convergence — Point solutions get absorbed into single workflow platforms.
- D5. Bargain Trap — A "cheap" name adjacent to a platform that must absorb it isn't a bargain.

**Strategy & Decision-Making**
- E1. Three-Box Test — (1) Does the category support a big winner? (2) Is this team positioned to win it? (3) Am I paid for the remaining risk at this price?
- E2. Fight / Focus / Fly — Under pressure, companies pick one: fight the incumbent, focus on a niche, or fly above into a new category.
- E3. VC is a Decision Business — Output is decisions, not activity.
- E4. Money as Truth Serum — Don't tell me what you think — tell me what you do. Insider follow-on, GP commit, founder pay reveal belief.
- E5. Start with Profit, Not Revenue — Real businesses are judged on profit and the cash it converts to.
- E6. Convert Revenue Multiples to Cash — VC works because the IPO/M&A window monetizes high revenue multiples before companies earn them in FCF.

**Raed's 3 filters (the local layer on top of the global frames)**
- Raed: Founder Obsession — Concrete evidence of grit, recruiting power, and customer obsession. Not background credentials.
- Raed: Market Scale — GCC TAM consistent with exit proceeds of ~$150M on a $3M check (validated ownership × validated exit multiple).
- Raed: Unfair Advantage — The one thing that compounds. If it takes a paragraph to describe, it isn't real.

---

## How to use this file when evaluating a new opportunity

When a new deal arrives, the recommendation engine should:

**Step 1: Retrieve similar precedents.**
Match the new deal to the 5–10 most similar historical rows by: sector (column D), geography (extract from D), stage (extract from G), Year cohort (column C), and Thesis Tags (column H). Look at both the StronYes/Outperforming rows (what worked in this category) and the No/Underperforming rows (what failed in this category).

**Step 2: Cross-check kill criteria against current state.**
Read column E (Original Thesis) for the matched precedents. Each thesis ends with a "we're wrong if [killer condition]." Treat those killers as hypotheses to test against the new deal — are any of them visibly true in the new opportunity?

**Step 3: Apply the named Mental Models.**
For each matched precedent, the Mental Models Applied column (AD) lists 3–6 frames used to interpret that deal. Apply the same frames to the new deal. If a similar deal failed under C2. Hubristic Financing or B4. Pulled-Forward Demand, the same risk should be priced into the new opportunity.

**Step 4: Look for Disagreement-row patterns.**
Filter to column AM = TRUE. These 15 rows are the failure modes that the original thesis missed. If the new deal's pitch resembles the rationale on one of those rows, treat it as a yellow flag — investigate whether the same disagreement could repeat.

**Step 5: Score against the 5 signal slots.**
For the new deal, score each of the 15 signal types as a forward-looking expectation: which 5 signals are most likely to determine the outcome of this bet, and what direction + weight do we expect on each? Then check those expected signals against how the same signals played out in the matched precedents.

**Step 6: Apply Raed's 3 filters with explicit kill criteria.**
- Founder Obsession: state the concrete grit/recruiting/customer evidence — not credentials.
- Market Scale: compute the GCC TAM that would support a $150M exit on the Raed check size; if the TAM isn't there, flag.
- Unfair Advantage: name the one thing that compounds, in one sentence. If it takes a paragraph, it isn't real.

**Step 7: Produce a recommendation in the same structure as the file.**
Generate a draft row for the new deal with: Original Thesis (one falsifiable sentence with kill criterion), Why Now, expected signals, mental models that should be tracked, and a predicted Hindsight Verdict that the actual IC should validate or override.

---

## What to weight more vs. less in training

Not all rows carry equal training weight. Suggested prior:

**Highest weight (most diagnostic):**
- Rows where AM = TRUE (15 rows) — these teach what we systematically get wrong.
- Rows where Hindsight Verdict = STRONG YES + Performance = OUTPERFORMING (decision and outcome both clean — gold-standard pattern).
- Rows where Hindsight Verdict = NO + Performance = UNDERPERFORMING or WRITE-OFF (decision and outcome both clean negative — what to avoid).

**Lower weight (noisier signal):**
- Rows where Performance = TOO EARLY (insufficient observation window — the outcome reading is provisional).
- Rows where Performance = UNKNOWN (Hala, where the IC memo wasn't retrieved — explicitly flagged as a data hygiene gap).
- Rows from 2016–2019 era (some lack IC memos in Drive; the Decision Rationale was reconstructed from training knowledge rather than from primary source). Specifically: Foodics, Crowd Analyzer, Tamatem, Golden Scent, Salla, Unifonic, Noon, Syarah, Hala, Mozn carry this caveat.

**Highest weight in the disagreement set specifically:**
- Halo AI — top-tier founder pedigree didn't translate to GTM execution in MENA.
- Omniful — significant FMV markdown; OMS/WMS/TMS pricing model didn't match SMB customer mix landed.
- Opontia — peak-hype-cycle entry into an aggregator thesis that collapsed globally.
- Signit — the moat that mattered (DGA TSP license) was underweighted at investment vs. the moat we underwrote (Arabic-first localization).
- ClearGrid — the personnel-on-which-the-bet-was-made (CTO Al Shehab) left within 12 months.
- WhiteHelmet — memo trajectory (5.9x YTD) didn't continue post-investment (~1.6x).

---

## Known limitations of this dataset

To be honest about what the model is reading:

1. **Sample size is small** (42 rows). Statistical inference will be unreliable; the file is more useful as a structured case library than as a labeled training corpus for supervised learning.

2. **Outcomes are not all settled.** 10 rows are TOO EARLY (recent 2024–2026 investments where post-investment data is sparse). Those rows carry low signal on outcomes.

3. **Some IC memos were not retrieved.** Hala, Mozn, Noon, Foodics, Crowd Analyzer, Tamatem, Golden Scent, Salla, Unifonic, Syarah — older investments where the formal IC memo wasn't surfaced in Drive search. For these, Decision Rationale was reconstructed from external knowledge + the original portfolio file's summary. They are explicitly flagged in the row's Decision Rationale.

4. **Visible reporting cadence varies.** Some companies report monthly metrics with high fidelity (Signit, SiFi, Aya); others have empty Metrics tabs (Deep.sa, Tasheelat) despite a configured reporting cadence. Where Visible is empty, "What Happened After" relies on Drive documents + public data + reasoned inference.

5. **Mental models are applied, not derived.** The named frames in column AD are best-fit retrospective applications, not predictions made at IC time. The training signal from these is "which frames retrospectively explain which outcomes," not "which frames were named in the original memo."

6. **Cross-fund vintage matters.** Some rows reflect Raed II era investments (ShopUp explicitly; some 2016–2018 era companies implicitly). These were made against a different fund thesis and target return profile than Raed III. The model should treat fund-vintage as a moderator, not aggregate across funds blindly.

7. **The Munaseb row contains an unresolved data contradiction** — Munaseb appears on both the historical PASS list (in earlier Copper analysis) AND the Visible active-investment list ($500K Seed Dec 2025). The contradiction is preserved in the Decision Rationale field rather than resolved.

---

## Quick reference: the most-cited Lessons across the file

Patterns that recur in column AL (Lessons) often enough to deserve weight in retrieval:

- **Regulatory-license-as-window** — Signit (DGA TSP), SiFi (EMI), Lean (SAMA). Licenses provide 12-24 months of first-mover advantage, then erode. The window must be converted to logo lock-in.

- **Egypt single-country FX risk** — Trella, Grinta, Taager. Egypt-anchored businesses carry irreducible FX/macro risk that can dominate operational execution.

- **Asset-light freight is structural-margin-failure** — Trella, Trukker, Convoy globally. Two consecutive same-category losses = pattern, not coincidence.

- **POS-to-platform bundling** — Foodics (POS → payments → lending → payroll), Salla (e-commerce → payments → apps marketplace). Proven SaaS-in-vertical playbook when founder team executes the bundling.

- **Pre-PMF in crowded categories is expensive optionality** — Halo AI, Bloompath, Elements. Productivity tools at >$10M post pre-revenue carry high category-convergence risk.

- **Cohort retention in B2B BNPL is project-driven not subscription-driven** — Aajil cohort retention dropping 100%→13% is structural, not a problem.

- **Top-tier founder pedigree ≠ MENA GTM capability** — Halo AI (Snapchat/Meta/AWS team couldn't crack MENA agency-dominated sales). Distribution insight is a separate skill from product-shipping pedigree.

- **Tracker → Pre-A → Series A in 15-18 months** — Aya, SiFi, Signit. The playbook for fast-conviction follow-on when founder + market + UA all align.

- **Strategic acquirer exits at $50–200M** — Golden Scent (Chalhoub). Excellent risk-adjusted returns but sub-fund-mover for venture math.

---

## End

The receiving LLM should treat this file as a structured case library: retrieve by similarity, test the new deal against the kill criteria of matched precedents, apply the named mental models, weight disagreement rows heavily, and produce a draft row in the same format as a recommendation for the human IC to validate.

Do not aggregate across funds without checking vintage. Do not over-weight the TOO EARLY rows. Do not assume agreement rows are diagnostic of skill — they may be diagnostic of luck. The disagreement rows are where the actual learning lives.
