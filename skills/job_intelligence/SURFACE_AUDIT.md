# Surface Audit — too many interfaces, overlapping surfaces

Date: 2026-08-04. Audits the CLI surface exposed to the orchestrator (and the
operator) across all entrypoints.

## Current surface: 8 CLIs, ~90 commands

| CLI | Commands | Role |
|-----|----------|------|
| apply.py | 12 | apply pipeline (detect/act/verify/shadow/creds/registry/...) |
| reach.py | 10 | outreach (discover/email/message/connect/...) |
| extract.py | 6 | stage-1 intake gates (admit/reject/reset/submit) |
| enrich.py | 8 | stage-2 enrichment gates (admit/flag/open/retry/...) |
| tailor.py | 8 | stage-3 tailoring gates (admit/ground/review/retry/...) |
| stage_emails.py | 2 | email ingestion |
| linkedin.py | 4 | LinkedIn scraper |
| ji.py | 15 | the orchestrator surface (built this session) |
| report.py | 31 | evidence + decision + config surface |

## Overlap findings

**1. `ji` is incomplete as the orchestrator surface.** It only wraps 4 report
commands (applied/audit/diff/shadow). 27 report commands and 10 apply commands
are NOT reachable through `ji` — the orchestrator still needs both `apply.py`
and `report.py` memorized. That's the core "too many surfaces" problem: the
single-surface goal wasn't finished.

**2. Stage-gate verb explosion.** extract/enrich/tailor each re-implement
`admit/reject/retry/undo/reset` with slightly different signatures. The
orchestrator must remember which CLI owns a stage. These should be ONE surface
with a `--stage` dimension, not three.

**3. Config/decision commands scattered.** `rules`, `keywords`, `domains`,
`spc`, `ingest`, `widget-draft` live in report.py but are *config/adapt*
commands, not evidence. They don't belong in the evidence surface.

**4. `verify` ambiguity.** `apply.py verify <jid>` (post-submit check) and
`ji verify <jid>` (risk-value review) are DIFFERENT commands with the same
name. Confusing for the orchestrator.

## Target: 3 surfaces

1. **`ji`** — THE orchestrator surface. Every action, evidence, and decision
   command, with the return contract. Wraps the existing modules; never
   re-implements.
2. **The pipeline CLIs (apply/extract/enrich/tailor)** — kept as the
   deterministic engine, callable by the orchestrator but not the surface it
   memorizes.
3. **reach.py** — outreach (a distinct domain, self-contained).

## Consolidation plan

1. **Complete `ji`** — **DONE**: `ji` now forwards every report.py and apply.py
   command via `_REPORT_CMDS`/`_APPLY_CMDS` (a validated superset; lint's
   CLI-docs check covers the full surface). The orchestrator memorizes `ji`
   only. `python3 ji.py stats|fleet|...|act|detect|...` all forward.
2. **Remove the `verify` ambiguity** — **DONE**: documented in SKILL.md —
   `ji verify` = risk-value review; `apply.py verify` = post-submit check.
3. **Keep the stage-gate CLIs** — extract/enrich/tailor stay as the engine;
   `ji` is the documented orchestrator path (SKILL.md updated).
4. **Update lint CLI-docs check** — **DONE**: `_report_commands` now reads
   ji's `_REPORT_CMDS`/`_APPLY_CMDS`/`_STAGE_CMDS` sets so the superset is
   validated.
5. **SKILL.md** — added the "Orchestrator surface (`ji`)" section with the
   two-verify clarification.
6. **Namespace the stage engines under `ji` (v2)** — **DONE this pass**:
   `_STAGE_CMDS` forwards `extract`/`enrich`/`tailor`/`reach`/`linkedin`/
   `stage_emails` through `ji <stage> <verb> ...`. This closes the last gap —
   before, `reach.py` (10 cmds) and the three stage-gate CLIs were NOT
   reachable via `ji`, so the "ONE surface" claim was false. It also resolves
   the verb collision from finding #2 without merging stage semantics: `ji
   apply undo` ≠ `ji reach undo` ≠ `ji tailor undo` (they keep their own
   behavior; only the namespace is unified). Verified by lint CLI-docs check
   (`_STAGE_CMDS` read as dispatchable).

## Second-pass audit (this pass) — what was still exposed, and the fix

Re-audit against the current code, entry point by entry point:

| Entry | Commands | Finding | Action |
|-------|----------|---------|--------|
| apply.py | 12 | fine, forwarded | — |
| reach.py | 10 | was the biggest unreachable surface | `ji reach <verb>` now forwards |
| extract.py | 6 | stage gate | `ji extract <verb>` |
| enrich.py | 8 | stage gate | `ji enrich <verb>` |
| tailor.py | 8 | stage gate | `ji tailor <verb>` |
| stage_emails.py | 2 | fetch | `ji stage_emails [--days N]` |
| linkedin.py | 4 | scraper | `ji linkedin [--url] [--count N]` |
| ji.py | 15 | orchestrator | the ONE surface |
| report.py | 31 | evidence/config | forwarded (`_REPORT_CMDS`) |

**Findings this pass:**

- **F1 (fixed): `ji` was not a true superset.** `_STAGE_CMDS` did not exist;
  reach + the three stage gates + linkedin + stage_emails were unreachable via
  ji, contradicting SKILL.md's "ONE surface" claim. Now fixed — every engine is
  under ji.
- **F2 (documented): verb collision by design.** `flag`/`undo`/`retry`/`reject`
  mean different things per engine. Namespacing keeps them correct rather than
  merging them into one ambiguous verb. A blind merge (finding #2's original
  suggestion) was rejected because `cmd_admit` is stage-specific: extract gates
  on category, enrich on description-exists, tailor on factual grounding.
- **F3 (left as-is): `verify` double.** Kept distinct and documented (ji =
  risk review, apply = post-submit).
- **F4 (unchanged): stage_emails/linkedin are thin.** Both are intentionally
  lightweight; `ji stage_emails`/`ji linkedin` forward their raw flags.

**Not merged, with reason:** `lib/db/pipeline.py` is a thin compat shim over
`jobs.py` (delegates, doesn't duplicate) — leave it. `apply/common/filler.py`
(used by check.py) vs `fill_runner.py` (used by auto/fill) are a deliberate
split: filler = read-back verification, fill_runner = orchestration. Both stay.

Result: 8 CLIs → the orchestrator interacts with **1 surface (`ji`)**, and
every stage verb is reachable as `ji <stage> <verb>`; the engines stay callable
beneath. Full suite: 626 tests + 138 subtests, lint PASS.
