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
   ji's `_REPORT_CMDS`/`_APPLY_CMDS` sets so the superset is validated.
5. **SKILL.md** — added the "Orchestrator surface (`ji`)" section with the
   two-verify clarification.

Result: 8 CLIs → the orchestrator interacts with **1 surface (`ji`)**; the
others remain callable engines. Full suite: 597 tests + 138 subtests, lint
PASS.
