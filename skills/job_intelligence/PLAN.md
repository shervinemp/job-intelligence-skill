# Pipeline Plan — autonomous verification + orchestrator interface

Status: **ALL IMPLEMENTED.** Fixes 1/2/3/4, safety items F3/F5/A3, FAILURE_MAP
A1–A6/B1–B3/C1–C4, G1–G4, LLM_GAPS (deterministic + surfaces), the `ji`
orchestrator surface, and CLI cleanup are all done. lint PASS, 593 tests +
135 subtests. Date: 2026-08-04.
Companion docs: AUDIT_REPORT.md (incident), FAILURE_MAP.md (status),
LLM_GAPS.md (status), META_FLOW.md (adaptation), SEPARATION.md (layers).

---

## Completed (reference)

- Fix 1 (Antigua), Fix 2 (honest verification + risk split + prefilled),
  Fix 3 (vision second-observer on ambiguous risk read-backs + READY gate),
  Fix 4 (runtime rules, S2 gate, TTL, per-field learning, data-driven
  classifiers, no hardcoded answers).
- Safety: F2 new-domain approval gate (`report.py domains`), F3 session
  isolation, F5 per-jid lock, A3 local vision guard.
- FAILURE_MAP A1–A6, B1–B3, C1–C4 — all fixed.
- LLM_GAPS — all addressed (deterministic logic + orchestrator surfaces; no
  ask_api for text).
- G1 verification red-team harness; G2 post-submit confirmation
  (`report.py applied`/`applied-confirm`); G3 fleet health score
  (`report.py fleet`); G4 daily pacing (`apply_policy.json daily_cap`).
- `ji.py` orchestrator surface: status / decisions / answer / verify / ready /
  job / diff / audit / apply / submit / shadow / fetch / tailor / verify-applied
  / applied.
- CLI cleanup: `mappings` deleted, detect requires jid, `--no-verify` merged
  into `--quick`, `enrich retry --curl` reachable, 3× status removed.

## Status line

lint PASS, 593 tests + 135 subtests. The plan is complete.
