# Unrecoverable & Mistake-Output Failure Trace

A trace of every failure mode that is either UNRECOVERABLE (no path back to
correct state) or MISTAKE-OUTPUT (wrong value certified/committed). Each was
verified against the code and, where relevant, against the live fleet data.

## The confirmed live incident (recovered)

Two jobs (MongoDB, Dialpad) were marked `applied` with **no `applied_at`** and
no dossier. Root cause: `_determine_outcome` (submit.py) returned `"success"`
on any page showing "already-applied" text — **without checking it was the
target posting** (a redirected/stale page could show it for a DIFFERENT job).
The code's own comment named the class: *"a false positive that can never be
corrected (job already marked applied)."*

**This is THE unrecoverable mistake-output case** — and it was live in the data.
Recovered via `undo`; the underlying bug is now fixed.

## Category 1 — UNRECOVERABLE wrong-`applied` (false positive)

| # | Failure | Trace | Status |
|---|---------|-------|--------|
| 1 | Already-applied text on a non-target page marks the job applied | `_determine_outcome` line 33 returned success with no `_on_target` check; pre-flight had the check, the outcome path did not | **FIXED** — `target_url` now threaded through all 4 callers; pinned tests |
| 2 | Applied with no `applied_at` | `has_already_applied_text` path never wrote the timestamp | **SURFACED** — `report.py applied --suspects` lists them; 2 recovered |
| 1b | **Uncertain outcome certified applied** | `cmd_submit` called `mark_applied(jid)` on an UNCERTAIN outcome ("conservative — prevents duplicate"), certifying applied on a result we didn't know happened | **FIXED** — uncertain now stays `tailored` (submit_clicked set, no re-submit) and emits `submitted_uncertain`, routing to `verify` which certifies only on real success signals. Pinned by `SubmitUncertainNotApplied` |

## Category 2 — MISTAKE-OUTPUT that persists (silent poison)

| # | Failure | Trace | Status |
|---|---------|-------|--------|
| 3 | Wrong learned mapping, never contradicted | `learn_mapping` count≥2 → active forever; retraction only on explicit contradiction or `wrong` adjudication. If never adjudicated, permanent. | **MITIGATED** — B1 retraction + SPC + fleet health; not auto-reaped without a verdict |
| 4 | Wrong profile answer (source of truth) | profile.json wrong value reproduces on every job; `--suspects` flags it only after a `wrong` verdict | **MITIGATED** — `report.py profile --suspects`; needs the orchestrator to correct profile.json |
| 5 | Wrong runtime rule (global poison) | `rules add` now requires `--domain` + `--confirm` (S2 discipline); TTL reaps | **FIXED** — domain-scoped + confirmed |
| 6 | Wrong fill-method preference | `field_methods` learns a method that "succeeds" but fills wrong | **FIXED** — `wrong` verdict rejects the method (#4) |

## Category 3 — State-machine unrecoverable

| # | Failure | Trace | Status |
|---|---------|-------|--------|
| 7 | `advance_job` accepts ANY stage | No transition validation — a buggy caller can wedge a job in an impossible stage with no valid path back | **OPEN** — add a legal-transition table |
| 8 | `submit_clicked` stuck (crash between flag-set and click) | One-shot guard set, crash before click → next run investigates (never re-click) — recoverable via investigation | **MITIGATED** — the guard is one-shot-safe; manual `--force` after human confirms |

## Category 4 — Mistakes that verification now catches (were silent)

| # | Failure | Trace | Status |
|---|---------|-------|--------|
| 9 | Form reinterpretation certified verified ("6"→"60") | `_check_delta` certified any change | **FIXED** — `reinterpreted` → unverified |
| 10 | URN read-back ("urn:li:geo:...") certified | `StandardReader` returned the opaque id | **FIXED** — `__URN__:` guard → verify_failed |
| 11 | Wrong phone-country (Antigua) certified | country-unloaded + bare-code fallback | **FIXED** — no bare-code fallback when country known-but-unloaded; native options read |

## Remaining open (honest)

- **#7 (`advance_job` transition validation)** — the one real state-machine gap.
- **#3/#4** — a wrong learned/profile value that is never adjudicated stays.
  Mitigation exists but requires the orchestrator to run adjudication.
- **Lazy-loaded native `<select>` success path** (phone-country on CyberCoders) —
  safety done, success needs type-to-reveal for native selects.

## The two live-suspect jobs

`5680acca25f36476` (MongoDB) and `7c6ddc99267d4160` (Dialpad) were the
recovered instances of #1/#2. Both are now `tailored` (re-verifiable) and
`applied --suspects` is clean.

## Test coverage added
- `AlreadyAppliedTargetGuard` (2 tests): on-target counts as success; non-target
  does not — pins the unrecoverable-guard.
