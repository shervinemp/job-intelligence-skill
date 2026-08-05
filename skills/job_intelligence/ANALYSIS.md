# Analysis — pros/cons of each design artifact

A critical review of every MD file produced in this effort, plus the live code
it describes. The goal is to decide what to keep, what to change, and what to
cut before/while implementing. All six files are design documents; none has yet
been implemented.

---

## 1. FUNCTIONAL_FLOW.md (54 steps + L1–L6 standing particles)

**Purpose**: flat, technology-free inventory of what happens to data and where
judgement is exercised. No hierarchy, no module bias.

**Pros**
- Genuinely flat — no stages imposed; the reader can re-sequence. This achieved
  the stated goal (avoid bias from current abstractions).
- Plain language (scrubbed of tech terms) makes it readable by a non-engineer
  and forces the author to think about *flow*, not *files*.
- The fixed per-step template (In/Do/Out/Decide/Observe/If-it-can't-tell) is
  excellent: `Observe` vs `Decide` is the core epistemic distinction, and
  `If it can't tell` captures the fail-mode contract everywhere.

**Cons**
- **54 steps is a lot of prose.** It's a good *reference*, a poor *working
  document* — too long to keep in the orchestrator's context. Needs a 1-page
  digest (the graph partly serves this).
- **No IDs in the steps beyond numbers.** The numbers are reused inconsistently
  (P-numbers in the graph vs bare numbers here), so cross-referencing the graph,
  ORCHESTRATOR_MAP, and ALGORITHMS is error-prone. The L1–L6 particles use a
  different namespace than the steps.
- **Ambiguity between steps 5/12 (dedup) and 30/31 (read-back vs compare)**:
  the flow treats them as separate steps, but they're one mechanism in code.
- **No ownership tags.** Who decides (code / orchestrator / human) is deliberately
  absent — which matches the "no bias" goal, but means the document can't be used
  alone to route work; it depends on ORCHESTRATOR_MAP.
- **Not validated against code.** Claims like "the three counts sum to the total"
  (step 35) are true (`terms.summarize`), but nothing in the doc says so; a
  reader must trust it.

**Verdict**: keep as the canonical *flow* reference; fix the ID namespace; add a
one-line "realized by" pointer per step to the module/function. Do NOT add
ownership tags (that's ORCHESTRATOR_MAP's job — duplicating it would reintroduce
the bias the doc exists to avoid).

---

## 2. ORCHESTRATOR_MAP.md (S1–S8 surfaces, C1–C9 contracts, involvement scoring)

**Purpose**: who decides what (the corrected scoring), what the orchestrator
reads (surfaces), what it must return (contracts).

**Pros**
- **The corrected scoring is the single most valuable analytical result in this
  effort.** It replaced the naive "can the LLM do this?" with the three-question
  test (only-one-can, cheap+high-frequency, feeds-learning), and it *explicitly
  retracts its own earlier over-assignments* (dropped category-alone, semantic
  dedup, contradictions, inbox-verify, re-examine). That self-correction is the
  right intellectual move.
- **The contracts (C1–C9) are machine-parseable one-liners** — exactly what an
  orchestrator loop needs. `ANSWER <jid> "<label>": "<value>"` is unambiguous.
- **C6 (vision confirm) draws the certification boundary correctly**: LLM returns
  YES/NO/CANNOT-TELL, code stamps `verified`. This is the Antigua fix at the
  contract level.
- **C9 (escalation question)** closes the loop that was previously just a
  HANDOVER flag — the human gets a structured, answerable question.

**Cons**
- **The surfaces and contracts are not yet bound to real commands.** S1–S8 /
  C1–C9 have no `ji` subcommand behind them; mapping them is PLAN.md step 5 but
  nothing pins the correspondence. Risk: the contracts drift from what the CLI
  actually emits.
- **C7 (fleet decision) is underspecified.** What *triggers* a fleet review? The
  ALGORITHMS doc answers this (SPC trip, drift alarm), but ORCHESTRATOR_MAP
  doesn't reference it. The contract exists without a trigger.
- **No error/contract-violation handling.** What does the pipeline do if a C
  reply is malformed, late, or contradictory? The fail-mode contract exists in
  FUNCTIONAL_FLOW's steps but not in ORCHESTRATOR_MAP's contracts.
- **Involvement scoring table has no file:line evidence.** Unlike AUDIT_REPORT,
  the scoring isn't tied to code, so a reviewer can't verify "P13 fit review is
  orchestrator-core" against the real `enrich`/`tailor` path without digging.

**Verdict**: keep the scoring and contracts; add (a) contract-violation
behavior, (b) trigger references to ALGORITHMS, (c) a binding to `ji` commands.

---

## 3. ALGORITHMS.md (H/E/S/D handoff + self-correction; Part 5 adversarial; Part 6 F/G)

**Purpose**: the *how* — when to escalate, how to do it cheaply, how the system
learns from mistakes, and the adversarial cases each closes.

**Pros**
- **H1 (observability gate) is the single most important algorithm.** "Hand off
  iff not observable" is the formalization of the whole philosophy, and it
  directly names the Antigua failure (a confident-but-unverified self-report).
- **H3 (belief accumulation) is implementable and testable** — a Bayesian/noisy-OR
  combination of independent signals with "echo adds no information" as the
  explicit rule.
- **S2 (correction buffer → rule authoring) is the learning loop's engine** and
  the priority ordering (H1+H3 → S1+S2 → E1+S3 → S4+D1 → S5) is sensible.
- **Part 6 (F1–F6, G1–G7) added genuine second-order analysis** — poisoned
  read-back, first-contact domain approval, session isolation, mid-fill drift,
  and the red-team harness. G1 (verification adversarial harness) is the best
  single investment.

**Cons**
- **S4 (wrong-fill SPC) and D1 (change-point drift) are aspirational.** They need
  a data pipeline (per-platform adjudicated rate over time) that doesn't exist
  yet. Marked correctly as "needs wiring," but they're the furthest from
  shippable.
  _(Status: since this analysis was written, both shipped — SPC as
  `report.py spc` + auto-pause, drift via `registry drift` + the form-drift
  check; the adjudicated ledger now exists.)_
- **S5 (Thompson-sampled widgets) is speculative** — the probe cascade is a
  deterministic priority list, not a bandit; converting it is a research task,
  not a fix.
- **G2/G3/G4 (post-submit polling, fleet health, pacing) are operational, not
  code** — they're cron jobs and config, easy to over-promise.
- **Part 5's status marks are aspirational** — `COVERED` for D1 (learning
  retraction) is optimistic; the retraction path exists in `resolve` only for
  `answers_override` contradictions, not a general wrong-answer mechanism.

**Verdict**: keep H1/H3/E1/S2/S3 as the implementable core; keep Part 6's F/G
as the roadmap; move S4/D1/G2/G3/G4 to a "later" section so the build order
doesn't promise them too early.

---

## 4. SPLITS.md (the five seams)

**Purpose**: where to cut for modularity/control/observability.

**Pros**
- **The split principle is right** — "cut where the orchestrator needs a
  checkpoint, an audit boundary, or a controlled mutation; never split
  mechanics." Applying it to the graph (checkpoint gates, read/write arrows)
  made the architecture visible.
- **Split 3 (fill vs verify) and Split 4 (learning store) are the two that
  matter** — they're exactly where the Antigua and self-correction work lives.

**Cons**
- **Splits 1 and 2 are architectural renames of what already works.** Making
  every C-point a hard STOP and making L2 write-only is a large refactor with
  migration risk and no immediate user-visible win. Worth doing *after* the
  correctness fixes, not before.
- **Split 5 (submit atomicity) already exists in code** (submit.py's flag-before-
  click + investigate-never-re-click); SPLITS.md describes it as a goal rather
  than acknowledging it's ~80% there.

**Verdict**: implement split 3 and 4 now; defer 1, 2, 5 (or fold 5 into the
F5 per-jid lock item which adds the missing cross-process part).

---

## 5. FLOW_GRAPH.md / FLOW_GRAPH.png

**Purpose**: visual rendering of the whole system.

**Pros**
- Renders (verified) and shows the loops the text hides: retry, undo, learning
  feed, re-check, multi-part advance.
- Checkpoint gates (▼) and read/write arrows encode SPLITS.md directly.

**Cons**
- **Dense at 1568×936** — legible but not scannable. Needs stage-level sub-graphs
  for actual use.
- **C2 checkpoint is a per-label loop drawn as a single node** — accurate but
  under-explained; a reader may misread it as one decision per form.
- **P7→C1 vs C7 edge semantics** were corrected, but the intake/enrich boundary
  still flows P7→CKPT_C1→P8→...→P13→CKPT_C1, which reads as two visits to the
  same gate. Acceptable but slightly confusing.

**Verdict**: keep as the overview; if time permits, render one sub-graph per
stage.

---

## 6. PLAN.md (build order)

**Purpose**: the execution plan.

**Pros**
- Ordered, scoped, and now extended (Fix 1–4 + safety items + ji + cleanup).
- Open questions 1–5 are genuinely open (risk-split, PII carve-out, promotion
  gate, scope, G1-first).

**Cons**
- **Scope has grown large.** Fixes + F/G items + ji + cleanup is a multi-session
  effort; PLAN.md doesn't separate "this session" from "later."
- **Question 5 (G1 first) is already answered by the analysis** — G1 is the right
  first move, so asking it is indecision; the plan should just do it.

**Verdict**: keep, but add a "This session vs later" boundary so the work is
executable in one pass.

---

## The live code's own issues (from AUDIT_REPORT + verification)

- `resolve.py:267-272` phone-country-code returns `+N` not country — **confirmed,
  the Antigua root cause.**
- `match.py:115-135` COUNTRY_ISO exists but is keyed off a *country* answer, so
  a `+1` answer never uses it — **confirmed.**
- `value_reader.py:161-182` FuzzyComboboxReader returns first textual match —
  **confirmed.**
- `fill.py:138` marks `verified` unless `_diag.get("unverified")` — the echo/empty
  read-back passes — **confirmed.**
- `apply_policy.json` absent ⇒ resolves to `hold` — **safe by default, confirmed.**
- Tests: **527 passed, 122 subtests** (re-run this session); lint PASS.

---

## What this analysis changes

1. **Implement now (this session)**: Fix 1 (resolver+matcher), Fix 2 (honest
   verification + risk split), C1 (prefilled kind), F1 (sanitized read-back),
   G1 (red-team harness), A3 (local vision guard). These are code + tests.
2. **Defer (later session)**: splits 1/2/5, S4/D1/G2/G3/G4 operational items,
   S5 bandit, ji surface (step 5), CLI cleanup (step 6).
3. **Doc corrections to make as I go**: unify step IDs (P-numbers in all docs);
   add contract-violation behavior; bind C-contracts to ji commands in step 5.
