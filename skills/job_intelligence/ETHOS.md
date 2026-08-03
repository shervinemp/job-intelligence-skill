# ETHOS — The Operating Contract

> The entire value of this pipeline is **trust**: you must be able to act on an
> outcome without auditing how it was made. Every design decision below serves
> that: code where precision matters, a local LLM only where code is blind, and
> an orchestrator LLM-in-the-middle as the operator, verifier and debugger —
> grounded in a mechanically produced evidence trail.

## 1. The three-layer trust model

**Layer 1 — Deterministic core = source of truth.** Everything in the hot path
(resolve → validate → fill → re-read verify → check) is code. No LLM sits
between a field and its value. Determinism buys: reproducibility, testability,
cost, and — critically — verifiability: a fill is verified by *re-reading the
form with the same scorer that picked the value*, mechanically.

**Layer 2 — ask_api = selective escape hatch, never default.** The served
local model is the WEAK model; the ORCHESTRATOR is the STRONG model.
Routing hierarchy — deterministic core first; the ORCHESTRATOR is the
semantic fallback of choice (no_match / no_option_match fields surface
with full evidence — candidates, top_options — in dossiers, and the
orchestrator answers them post-hoc via --answers, then re-fills); the
served local model is reserved for where the orchestrator PHYSICALLY
cannot act (vision — it has no page access) or live-page interactions
that cannot wait; the user is the final authority.

| kind | default | when |
|---|---|---|
| `vision` | on | image processing — the orchestrator cannot see the page |
| `option_pick` | off | the orchestrator decides from `top_options` evidence |
| `gap_fill` | off | the orchestrator decides from dossier evidence |
| `batch_verify` | off | LLM re-reviewing ALL fields lowers accuracy |
| `verify_reads` | off | deterministic re-read + check is the verifier |
| `auto_retry` | off | failures surface as evidence, never hidden |

`JI_LLM_MODE=off | auto | on`. Every LLM output must pass the *same*
deterministic validator as every other fill — the escape hatch never
bypasses the code's truth.

**Layer 3 — Orchestrator (LLM-in-the-middle) = the operator.** The pipeline is
*designed to be operated by* an overarching orchestrator LLM, not to run
unattended. The orchestrator runs batches, classifies outcomes, fixes failure
classes (code bug / data gap), arbitrates with evidence, and routes only
personal decisions to the user. The evidence trail is the orchestrator's
working memory; `report.py` commands are its toolset; the shadow run is its
test harness (run → classify → fix → re-run → diff).

## 2. Why — the foundation in LLM architecture

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
evidence trail as the checksum on every judgment.

## 3. The handoff seams

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
   guess, and a wrong guess is the most expensive failure mode. Handovers are a
   precious resource — the funnel routes to the user only what is not
   answerable (121 jobs → 47 ready → 4 user-decides).

## 4. Operating principles

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

## 5. The honest ledger

Status: all ten items addressed 2026-08-03 (verified by tests + live runs).
Residual discipline: each fix must keep its test; the ledger re-opens on
new evidence.

1. **Gap-fill validation bypass — FIXED.** Unmatched labels are DROPPED
   (fail-closed), lookup is truncation-tolerant, dropped mappings are
   audited (GAP_FILL_GATE line). Tests in test_ethos_gaps.py.
2. **Tailor factual-grounding gate — FIXED.** lib/grounding.py checks
   every company/title/date/degree against profile.json; novel claims
   quarantine the artifact; `tailor.py admit` is blocked until clean
   (--force = human review override); `tailor.py ground <jid>` prints
   the manifest.
3. **Pre-flight profile gate — FIXED.** `apply.py preflight` prints the
   manifest (hard/soft/answer-gaps/coverage); the shadow supervisor
   warns before burning browser time; submit warns on late gaps.
4. **Learned-mapping hygiene — FIXED.** pending→active after 2
   consistent confirmations; TTL expiry (90d); provenance; an explicit
   contradicting answer invalidates the mapping; conflicting
   confirmations reset the count.
5. **Canary enforcement — FIXED.** Submit REFUSES (--force override)
   when the latest dossier regressed fields vs the previous run; the
   classify ready-list excludes regressed jobs (QUARANTINED).
6. **Instrumentation — FIXED.** llm_status (policy_off / api_down /
   declined / used) recorded per escape-hatch call, aggregated into the
   dossier, probed at batch start, surfaced in classify.
7. **Cross-field coherence — FIXED.** apply/common/coherence.py:
   sponsorship↔authorization, city↔province, pronouns↔gender; findings
   are ERRORs that block submit; evidence-attached.
8. **Corpus snapshots — FIXED (core).** capture() and capture_from_html()
   scrub PII before storage; capture-on-fix workflow documented; golden
   replay harness pre-exists (mock_page + registry drift).
9. **Unconfirmed-skip follow-up — FIXED.** cause-tagged (unconfirmed/
   recheck); `apply.py shadow --recheck` re-examines the queue; classify
   clusters unconfirmed skips by company (platform hypothesis).
10. **Fleet accuracy report — FIXED.** `report.py fleet`: kinds,
    filled%, METHOD ATTRIBUTION (the ethos's falsification), weekly
    trend, steering memo of top failing labels.

## 6. Metrics we steer by

- **Wrong-fill rate** (per field class, per platform) — the falsification
  instrument for the ethos itself; the orchestrator's calibration feedback.
- **Batch survivability** — zero silent deaths; every crash becomes evidence.
- **Orchestrator cycle cost** — iterations (fix → re-run → diff) per failure
  class; evidence must be actionable at a glance, in finite context.
- **Handover precision** — false handovers minimized via profile truth, not
  keyword heuristics.

## 7. Guardrails for the orchestrator (me)

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
- **The evidence trail is the checksum on every judgment** — including mine.

## 8. This document

A living contract. Amendments are improvements to trust, never license for
shortcuts. When in doubt between two designs, choose the one that makes the
next failure easier to see.
