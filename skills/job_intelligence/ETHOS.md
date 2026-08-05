# ETHOS — The Operating Contract

> The entire value of this pipeline is **trust**: you must be able to act on an
> outcome without auditing how it was made. Every design decision below serves
> that: code where precision matters, an orchestrator LLM-in-the-middle as the
> operator, verifier and debugger, a served local model only where the
> orchestrator physically cannot act, and the user as the final authority —
> grounded in a mechanically produced evidence trail.

## 1. The three-layer trust model

**Layer 1 — Deterministic core = source of truth.** Everything in the hot path
(resolve → validate → fill → re-read verify → check) is code. No LLM sits
between a field and its value. Determinism buys: reproducibility, testability,
cost, and — critically — verifiability: a fill is verified by *re-reading the
form with the same scorer that picked the value*, mechanically.

**Layer 2 — The served local model = reserved, never default.** The weak model
is used ONLY where the orchestrator physically cannot act (vision — it has no
page access) or a live-page interaction cannot wait. Every LLM output must
pass the *same* deterministic validator as every other fill — the escape hatch
never bypasses the code's truth. `JI_LLM_MODE=off | auto | on`; in `auto`,
only `vision` is on (`option_pick` / `gap_fill` / `batch_verify` /
`verify_reads` / `auto_retry` are off — the orchestrator replaces them).

**Layer 3 — Orchestrator (LLM-in-the-middle) = the operator.** The pipeline is
*designed to be operated by* an overarching orchestrator LLM, not to run
unattended. The evidence trail is the orchestrator's working memory;
`report.py` commands are its toolset; the shadow run is its test harness; the
decisions inbox is its queue.

## 2. The routing hierarchy (who decides what)

The served local model is the WEAK model; the orchestrator is the STRONG model
and the intended operator. Every decision routes down this ladder — never
up:

1. **Deterministic core** — mechanical decisions, always first (cheap,
   precise, reproducible).
2. **Orchestrator** — the semantic fallback of *choice*. `no_match` /
   `no_option_match` fields surface with full evidence (candidates,
   top_options, selection_readback) in dossiers; the orchestrator answers
   them post-hoc via `--answers`, then re-fills. Stronger model, no per-field
   latency in the hot path.
3. **Served local model** — ONLY where the orchestrator physically cannot
   act: vision / image processing, or a live-page interaction that cannot
   wait for the orchestrator round-trip.
4. **User** — identity/legal/life decisions and live-send consent. Handovers
   are a precious resource; the funnel routes to the user only what is not
   answerable.

The evidence-backed queue is the orchestrator's: every item carries the
answer menu (`top_options`) and the answer command. The orchestrator is
involved in *every decision the core cannot make safely* — and deliberately
NOT in the mechanical ones.

## 3. Why — the foundation in LLM architecture

LLM weaknesses are **architectural**, not bugs that prompting or tuning can fix:

| root cause | induced weaknesses |
|---|---|
| training objective is plausibility, not truth | confabulation, poor calibration, non-reproducibility |
| parameters are lossy corpus compression | confidence without knowledge, out-of-distribution failure, cutoff |
| learned structure is co-occurrence, not causation | competence cliffs, compounding multi-step errors |
| no state, only context | context fragility, self-contradiction, blindness to the world |
| data and instructions are undifferentiated tokens | prompt injection, prompt sensitivity, sycophancy |

Implication: these can only be **compensated externally**. This design *is*
that compensation — determinism where exactness matters, mechanical
verification where self-check fails, sanitized inputs where injection lives,
external memory (files, dossiers, summaries) where recall fails, and the
evidence trail as the checksum on every judgment. The ORCHESTRATOR shares
these weaknesses but is the strongest model available — which is why the
hierarchy prefers it over the served local model.

## 4. The handoff seams

Each seam sits exactly where the *nature of the decision* changes:

1. **Hot path → evidence.** Every fill ends in a dossier: `kind`
   (`verified / unverified / needs_data / rejected_by_form / interaction_failed`),
   `_diag` evidence, counts that sum. Truth is written at the moment of action —
   the page state won't exist later. The hot path never blocks on judgment.
2. **Evidence → orchestrator.** Failure-classification needs batch context
   ("has this platform ever worked?") which cannot live in a field decision.
   The owner-split (code / data / handover) is argued from *profile truth*
   (`_has_answer`), not keywords alone.
3. **Orchestrator → user.** Identity/legal/life decisions (sponsorship, essays,
   EEOC, preferred name) and live-send consent. Any automation would be a
   guess, and a wrong guess is the most expensive failure mode.

## 5. The decisions inbox (handovers)

ONE surface for every open decision, grouped by OWNER, with the evidence to
decide without opening the dossier and the exact answer command:

| owner | content | who decides |
|---|---|---|
| USER | personal questions the profile can't answer | the user |
| ORCHESTRATOR | evidence-backed fields (`no_option_match` with `top_options`) | the orchestrator, from evidence |
| DATA | profile/answer gaps (work_history, missing classes) | the orchestrator supplies/asks for data |
| REVIEW | stopped/blocked jobs (login/captcha/2fa/regression) | investigate, then decide |

Answered items disappear when the dossier updates — no separate state.
`report.py handovers [owner]`; `report.py help` is the grouped surface map.
Surface discipline: **one command per question**; anything not earning its
place is removed (the legacy surface was pruned for exactly this reason).

## 6. Operating principles

- **Honest outcomes, no silent lies.** Verified vs unverified is an epistemic
  state, not a mechanism. Counts are mutually exclusive and sum to the field
  total. `confirmed expired` vs `unconfirmed (cookie/session variance)`.
  `login_required` is never a fake success.
- **Evidence over guessing.** Investigate before retrying. Silent deaths become
  tracebacks (faulthandler, subprocess isolation, outcome files). One job can
  never kill a batch.
- **One-shot semantics.** Submit and send are single-shot, flagged, never
  re-clicked. Uncertain outcome → investigate and report; never resend.
- **Ask when in doubt.** Profile/common-answers data before deciding; if the
  data doesn't cover it, ask — never assume.
- **Robustness by construction.** Subprocess workers, per-job wall-clock
  budgets, consecutive-failure aborts, atomic outcome files, resumable logs.

## 7. The operating loop (the orchestrator's procedure)

The orchestrator's day is a fixed loop — never improvised:

1. **Preflight** (`apply.py preflight`) — profile readiness + adaptability.
2. **Batch** (`apply.py shadow`) — evidence production, crash-proof.
3. **Classify** (`report.py shadow --classify`) — outcomes + owner split.
4. **Inbox** (`report.py handovers`) — every open decision, grouped by owner.
5. **Answer from evidence** — the ORCHESTRATOR queue via `--answers` +
   re-fill; USER queue surfaces for the user; DATA gaps → profile asks.
6. **Verify** — re-run the touched jobs; the regression canary arbitrates.
7. **Close classes permanently** — `report.py fleet` rule-candidates and
   `report.py widgets` become resolver rules and widget handlers.
8. **Sync manually** - review the change set on BOTH sides first; a one-way copy that cannot detect direction is poison (it would silently overwrite repo-side work). Copy only intended files, then verify with a symmetric two-way diff (every file equal, no orphans either side). Repo suite, commit, push.

## 8. The self-check commitment

The orchestrator's verdicts and completion claims are only as good as the
gates they pass. **No claim without a gate:**

- every change runs `scripts/lint.py` (vocabulary literals, dead strings,
  nested dirs, compile) -> full suite -> a live shadow job -> manual sync with a symmetric two-way diff (every file equal, no orphans either side). Repo suite, commit, push.
- verdicts cite evidence (`_diag` facts, dossier fields) — anti-sycophancy:
  never confirm a conclusion because it was expected;
- "fully wired" means the mechanical checks prove it, not the narrative;
- a sync tool that only copies one way is NOT a gate - hash equality is not proof of intent.

The failure mode this guards against is the orchestrator's own
self-verification blindness: the gates are the instruments, the same way the
pipeline's re-read is the form's instrument.

## 9. OOD posture

OOD cases degrade honestly along a designed curve — they never guess:

1. capability-hash routing + probe router absorb new platforms dynamically;
2. `a11y_reader` (AX tree) catches closed shadow DOM;
3. `report.py widgets` is the unhandled-widget backlog (visible, not silent);
4. `report.py fleet` rule-candidates convert repeated failures into resolver
   rules; the i18n layer absorbs foreign-language forms;
5. the served local model covers vision when up;
6. anything still unresolved becomes an inbox item — never a silent wrong
   answer.

Adaptability is the orchestrator's responsibility, not the pipeline's alone:
closing a widget class means a handler + registry entry + corpus snapshot.

## 10. Metrics we steer by

- **Wrong-fill rate** (per field class, per platform) — the falsification
  instrument for the ethos itself; the orchestrator's calibration feedback.
- **Batch survivability** — zero silent deaths; every crash becomes evidence.
- **Orchestrator cycle cost** — iterations (fix → re-run → diff) per failure
  class; evidence must be actionable at a glance, in finite context.
- **Handover precision** — false handovers minimized via profile truth, not
  keyword heuristics.
- **Queue velocity** — the inbox's clear rate: USER items answered, 
  ORCHESTRATOR items closed from evidence, DATA gaps filled.

**Enforcement status (be honest about which of these exist).** A metric
named but not computed is an unfalsifiable claim, which is exactly what
this section is supposed to prevent:

| metric | instrumented? |
|---|---|
| Wrong-fill rate | **YES** — the adjudicated fill ledger (`lib/db/fills.py` + `report.py wrongfill`) separates `kind` (did the value land?) from `verdict` (was it right?). Reported only over adjudicated fills, with the unjudged denominator shown. `report.py adjudicate` samples riskiest-first. |
| Batch survivability | YES — subprocess isolation, per-job budgets, atomic outcome files, `report.py shadow`. |
| Orchestrator cycle cost | **YES** — `shadow_run.jsonl` carries per-job seconds; `report.py wrongfill`'s ROOT CAUSE CLUSTERS (D6) group failures by label×platform×method so one fix clears a class, not one job. |
| Handover precision | **YES** — owner split (`report.py shadow --classify`) uses profile truth (`_profile_has_answer`), not keywords, to route USER vs DATA; `report.py profile --suspects` surfaces profile answers adjudicated wrong so false handovers get corrected at the source. |
| Queue velocity | **YES** — `report.py applied --unconfirmed` + `handovers` show the open-decision set over time; `ji status` gives the current READY/HOLD split as a one-line trend. |

The adjudicated ledger is the falsification instrument: `verified` still means
"filled as intended" (never "filled correctly") — but now *correctness* is
measured independently by adjudication, and a `wrong` verdict retracts the
learned mapping, drops the suspect rule, flags the profile answer, and feeds
the SPC tripwire (B1/B2). The probe router's success predicate remains
`field_count > 0` for completion; correctness is the ledger's job.

## 11. Guardrails for the orchestrator (me)

The orchestrator is an LLM with the same weaknesses; the guardrails exist to
constrain it:

- **Hold-mode by default**; live submits and sends only with explicit user
  consent.
- **Verdicts cite evidence** (`_diag` facts, dossier fields) — anti-sycophancy:
  never confirm a conclusion because it was expected.
- **Memory: files over recall.** Anchored summaries are lossy compression;
  treat them as such and re-verify from files.
- **Red lines** (AGENTS.md): no data exfiltration, no destructive commands,
  ask before anything leaves the machine.
- **The gates are the instruments** — lint, suite, live-verify, port; a claim
  without a gate is a guess.
- **The evidence trail is the checksum on every judgment** — including mine.

## 12. This document

A living contract. Amendments are improvements to trust, never license for
shortcuts. When in doubt between two designs, choose the one that makes the
next failure easier to see.
