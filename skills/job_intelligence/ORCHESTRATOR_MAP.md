# Orchestrator Map — involvement, surfaces, and contracts

The corrected picture: where the LLM orchestrator genuinely earns its context,
what it sees (surfaces), and what it must return (contracts) so the pipeline can
act deterministically. Built on the observability-boundary principle.

## Part 1 — Corrected involvement

Three questions decide if the orchestrator touches a step:
1. Is it the only thing that can do it?
2. Is the error cheap and the frequency high enough to justify context?
3. Does it feed the learning loop (make the next run cheaper)?

### High-leverage — the orchestrator earns its keep (6)

| Step | Why it's worth it |
|------|-------------------|
| 13 fit review (+ category) | Only entity that reads a description against criteria. High frequency. Core intake judgment. |
| 15 tailored draft | Only entity that can compose it. Irreplaceable. |
| 24 field meaning | Only entity on novel labels. High frequency. **Feeds learning → shrinks itself.** |
| 30/31 read-back verify | Only way to catch the Antigua class on ambiguous widgets. Vision-fed, code-stamped. |
| 38/39 submit outcome | Only way to determine "what actually happened" — prevents double-submit and false-negatives. Highest stakes. |
| 44 message composition | Only entity that can compose. Irreplaceable. |

### Fallback — real but narrow, keep lean (4)

| Step | The honest caveat |
|------|-------------------|
| 11 fact extraction | Only when structured data is absent; code handles the common case first. |
| 17 grounding resolution | LLM explains provenance (it generated the claims); the *verdict* is human. |
| 20/21/23 vision on OOD widgets | Already a last-resort probe. Real, low frequency. |
| 41/43 contact selection | Genuine judgment, per-job, low frequency. |

### Dropped — I over-assigned these (5)

| Step | Why it's not worth it |
|------|----------------------|
| 6 category alone | Merged into the fit verdict (C1) — one decision, not two. |
| 12 semantic dedup | Rare; false-positive cost real. |
| 34 semantic contradictions | Code catches logical pairs; leftovers handled at fit-review. |
| 46 inbox verify | A human is the reliable verifier. |
| 54 re-examine set-asides | Re-fetch + signals settles most; marginal LLM weight. |

### Not the orchestrator at all

- **36 submit mode**: the *human's* one-time policy decision (hold/live), not per-job.
- **52 outreach grounding**: reuse deterministic grounding (16), don't spin a second judgment.
- Safety (destination vetting), certification (`verified` stamp), one-shot actions + guards (L3): **hard no** — fail-closed deterministic.

### The role I originally missed — fleet-level, not per-step

The orchestrator's most tangible value is at **batch boundaries**, where one
judgment fixes many jobs and the human needs the summary anyway:

- **After a shadow batch**: classify outcomes, split owners (code/data/handover) → 10-line action list from 60 dossiers.
- **Recurring-problem detection**: "same label fails across 15 jobs" → fix the resolver once, fix 15 jobs.
- **Context compression**: each dossier → "blocking: 2 risk fields unverified, here are the values."

Per-job LLM only when *only it can do it* AND *it feeds learning*. Fleet-level
LLM always — judgment compounds there.

---

## Part 2 — Surfaces (what the orchestrator reads)

A surface is a read-only view. Each is compact by default, `--json` for full.

| # | Surface | Contents | Consumed by |
|---|---------|----------|-------------|
| S1 | **Decision inbox** | Open decisions grouped by owner (USER / ORCHESTRATOR / DATA / REVIEW). Each: jid-prefix, label, kind, evidence (top options + scores), the answer command template. | All Decide steps |
| S2 | **Dossier** (per-job) | Per-field kind + observed value + method, blockers, decisions, artifact links. History kept for diff. | 17, 24, 39, fleet |
| S3 | **Fit queue** | Described jobs: title, company, location, salary, description snippet, dedup-flagged. | 13 |
| S4 | **Fleet report** | Shadow outcomes, owner-split of stopped jobs, unconfirmed clusters, method attribution, top failing labels. | fleet review |
| S5 | **Outcome evidence pack** | Post-submit: signals seen, URL change, form gone, screenshot path, validation errors. | 38/39 |
| S6 | **Readiness** | Profile coverage, hard/soft gaps, answer gaps. | pre-batch |
| S7 | **Adjudication ledger** | Fills awaiting a verdict: intended answer vs read-back, platform, kind. The falsification instrument (wrong-fill rate). | C8 |
| S8 | **Session timeline** | Per-run event log (actors, actions, outcomes) — the raw evidence behind an investigation. | 38/39 |

### Context-budget rules for every surface

1. Default output is **aggregate-first**: counts, top-N, jid prefixes. Detail only via `--json` or an explicit `job <jid>`.
2. Values appear **only** on the review surfaces (S1's answer template, S2's observed values) — the sanctioned carve-out. Everywhere else, labels/keys only.
3. Every surface ends with the return-contract line (`NEXT:` / `DECISION:` / `READY:` / `DONE:`).

---

## Part 3 — Contracts (what the orchestrator returns)

A contract is machine-parseable, one-line, unambiguous. The pipeline consumes it
deterministically; the orchestrator never reports an *outcome* — code observes,
orchestrator decides. When the orchestrator can't decide, it returns `HANDOVER`.

| # | Contract | In (surface) | Return | Pipeline acts |
|---|----------|--------------|--------|---------------|
| C1 | **Fit verdict** | S3 | `ADMIT <jid> <category>` \| `REJECT <jid> <reason>` | advance to described / reject |
| C2 | **Answer resolution** | S1 / S2 | `ANSWER <jid> "<label>": "<value>"` | fill, verify, **learn** (domain-scoped) |
| C3 | **Grounding** | S2 (novel claims) | `FACT <jid> <claim>` \| `FIX <jid> <claim>` \| `FORCE <jid> <claim>` | add to profile / re-draft / admit |
| C4 | **Outcome verdict** | S5 | `SUBMITTED <jid>` \| `FAILED <jid> <reason>` \| `UNCERTAIN <jid> <what-to-check>` | mark applied / clear guard (validation only) / hold |
| C5 | **Contact choice + message** | S2 / S1 | `DRAFT <jid> <contact-idx> <text>` | stage message; **send only after human approval** (one-shot) |
| C6 | **Vision confirm** | S5/S2 + screenshot | `YES` \| `NO` \| `CANNOT-TELL` | YES→verified; NO/CANNOT→unverified (risk→block). **Code stamps, never the LLM.** |
| C7 | **Fleet decision** | S4 | `FIX <label\|code> <jid...>` \| `ANSWER <label> <value>` \| `HANDOVER <label> <jid...>` | compile answers to learning / escalate code fixes / surface human items |
| C8 | **Adjudication verdict** | S7 | `VERDICT <fill-id> correct\|wrong\|unanswerable [note]` | updates wrong-fill rate; **wrong feeds resolver/learning correction** |
| C9 | **Escalation question** | S1 / S6 | `QUESTION <owner> "<label>" <context> [options...]` | rendered to the human; the human's answer compiles to learning (same path as C2) |

### Contract rules

1. **One line, exact syntax** — no prose the pipeline must parse.
2. **The orchestrator decides, never certifies.** C6 is the boundary: the LLM returns `YES/NO`, but the `verified` stamp and any state change are code's.
3. **Uncertainty is a first-class answer** (`HANDOVER`, `UNCERTAIN`, `CANNOT-TELL`). Never a guess dressed as confidence.
4. **Every C2 answer compiles into the learning loop** — the label stops appearing in S1 on the next run.
5. **C4 is one-shot-safe**: `FAILED` clears the guard only when validation errors are the cause; `UNCERTAIN` always holds and never re-clicks.
6. **C8 is the falsification loop** — adjudication isn't bookkeeping; a `wrong` verdict feeds back into resolver/learning so the same wrong answer stops recurring.
7. **C9 is how the human is actually asked** — a bare `HANDOVER` flag isn't enough; the question must carry label + context + options so the human answers in one line, and that answer re-enters the learning loop.

---

## Part 4 — The invariants (what never crosses to the LLM)

- Safety decisions (destination vetting) — code only, fail-closed.
- `verified`/`unverified` certification — code only; the LLM feeds observation, not verdict.
- One-shot actions (submit, send, connect) and their guards (L3) — code only; only an explicit human override clears a guard.
- State persistence, archival, practice-mode suppression (L4, L5, L6) — code only.
- Pure mechanics (fetch, extract, render, lookup, retry, arithmetic) — code only.
