# ClarityNet — Benefits Navigator
### USAII Global AI Hackathon 2026 — Undergraduate Track, Challenge Brief 4 (Direction A)

## What it does

ClarityNet helps people quickly understand whether they may qualify for three
common public support programs in Texas — **SNAP** (food assistance),
**Medicaid** (health coverage), and **LIHEAP** (home energy assistance) —
through a short, guided conversation, then explains *why* in plain language
and gives concrete next steps.

## The user journey (demo persona: Maria)

Maria is a single mother of two whose work hours were just cut from 40 to
20 hours/week. She opens ClarityNet, answers six simple questions (household
size, income, state, household composition, employment status, and what's
going on), and within about a minute receives a clear breakdown:

- **LIHEAP: Likely match** — income and residency criteria both met
- **SNAP: Possible match — needs review** — income/residency met, but the
  work-requirement rule needs a caseworker to confirm
- **Medicaid: Possible match — needs review** — income criterion for
  children met, but adult eligibility and immigration-status rules need
  human review

For each program, ClarityNet shows required documents, next steps, and a
link to the official source — and reminds Maria this is a starting point,
not an official decision.

## AI Architecture

**1. Inputs**
A six-question guided intake collects: household size, monthly income,
state, household composition (children/elderly/disability), employment
status, and a free-text description of the user's situation.

**2. AI capabilities used**
- **Conversational AI** — guided, one-question-at-a-time intake with warm
  acknowledgements (GPT-4.1)
- **Retrieval** — a structured JSON knowledge base (`programs.json`) of
  eligibility criteria for SNAP, Medicaid, and LIHEAP, curated from
  Benefits.gov, Medicaid.gov, and the ACF LIHEAP program page
- **Classification** — a rule-based eligibility engine checks the user's
  profile against each program's criteria and labels each one
  `met` / `not_met` / `unclear`, then derives an overall confidence label
  (`Likely match`, `Possible match — needs review`, `Likely not a match`)
- **Recommendation** — next steps and required documents are surfaced per
  program based on the evaluation outcome

**3. Processing**
This is the key design choice: **eligibility logic is rule-based and
deterministic, not LLM-guessed.** For each criterion, the engine compares
the user's reported income against the federal poverty guideline (scaled
by household size and the program's specific multiplier — 130% for SNAP,
150% for LIHEAP, 200% for Medicaid's child income test), checks state
residency, and explicitly flags criteria that legally require case-by-case
human review (work requirements, immigration status, non-expansion-state
adult Medicaid rules) as `unclear` rather than guessing.

The LLM (GPT-4.1) is used *only* to (a) conduct the conversational intake
and (b) translate the structured, already-computed results into a warm,
plain-language explanation — it never decides eligibility itself and is
explicitly instructed not to recompute or contradict the structured results.

**4. Outputs**
- A per-program confidence label and plain-language explanation
- A visible breakdown of which specific criteria were met, not met, or
  unclear, with the reasoning shown for each
- Required documents and next steps
- A source citation for every program
- A persistent reminder that results are not an official determination

## Responsible AI

**Risk:** A user under financial stress could read "you may qualify" as a
guarantee and make decisions (e.g., assuming a benefit will arrive) without
verifying — or the system could misjudge eligibility for criteria that
genuinely depend on case-by-case or state-specific factors.

**Mitigation:**
1. The system **never** says "you qualify" or "you don't qualify" — only
   "may qualify," "possible match," or "needs human review."
2. Every result shows **which specific criteria** were met, not met, or
   unclear, with the reasoning spelled out — not just a final verdict.
3. Criteria that legally require case-by-case judgment (work-requirement
   exemptions, immigration status, non-expansion-state adult Medicaid) are
   **never guessed** — they are explicitly labeled `unclear` and flagged for
   human/caseworker review.
4. Every program result includes its official source and a reminder to
   apply through the official agency for a final answer.

**Human-in-the-loop:** ClarityNet does **not** make the final eligibility
determination, and it does not submit applications. Specifically, any
criterion depending on individualized circumstances — such as SNAP work-
requirement exemptions, Medicaid eligibility category for adults in a
non-expansion state, or immigration status — is explicitly routed to a
human caseworker rather than evaluated by the AI. This is because these
rules involve case-by-case discretion and legal nuance that a static rule
set cannot safely automate; only an authorized caseworker can make that
determination.

## Data Disclosure

- **Eligibility rules** (`programs.json`): manually curated from public
  sources — Benefits.gov (SNAP), Medicaid.gov, and the Administration for
  Children and Families LIHEAP program page — for the state of Texas.
  Poverty guideline figures are illustrative and should be verified against
  the current HHS publication before any real-world use.
- **User scenarios** (`personas.json`): three entirely synthetic personas
  (Maria, James, Aisha) created by the team to demonstrate the system across
  different household situations. No real personal data is collected,
  stored, or transmitted.

## Tools Used

- **GPT-4.1** (OpenAI API) — conversational intake and plain-language result
  generation (paid API, usage-based)
- **Streamlit** — web UI framework (free, open-source)
- **Python** — eligibility reasoning engine (free, open-source)
- Architecture and UI patterns adapted from a prior personal project
  (a university advising chatbot), rebuilt from scratch for this domain's
  data, reasoning logic, and responsible-AI requirements.

## Running locally

```bash
pip install streamlit openai
export OPENAI_API_KEY="your-key-here"
streamlit run app.py
```

## Files

- `app.py` — main Streamlit application
- `programs.json` — eligibility rules knowledge base (SNAP, Medicaid, LIHEAP)
- `personas.json` — synthetic demo personas
- `test_engine.py` — standalone test of the eligibility reasoning engine
