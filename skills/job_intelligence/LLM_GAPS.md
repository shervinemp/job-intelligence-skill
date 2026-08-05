# LLM-in-the-Loop Gaps — where the orchestrator should be but isn't

The inverse of ORCHESTRATOR_MAP (archived): every place the LLM could add judgment but is
currently absent, organized by the four domains. Each gap tagged against the
contract (C1–C9) it would use, its priority, and what blocks it today.

Constraint held throughout: these are JUDGMENT additions (Decide/Verify roles).
The LLM proposes; code decides; certification and one-shot guards stay code;
vision stays local-only (A3).

---

## Domain 1 — In the apply itself (recovery & boundary decisions)

| # | Gap | Current (no LLM) | Contract | Priority |
|---|-----|-------------------|----------|----------|
| A1 | **Validation-error → fix** | `submit.py:_get_validation_errors` is regex matching; on rejection emits `NEXT: act --fill` (fix validation errors) with no specifics | C2 `ANSWER <jid> "<label>": "<value>"` | **HIGH** — today a novel error = human investigation |
| A2 | **Submit-button disambiguation** | `_detect_submit_button` keyword scoring | C4 evidence (before click) | HIGH |
| A3 | **`--next` vs `--submit` decision** | `_NEXT_KEYWORDS_JS` keyword list | C4 | HIGH — the one-shot boundary |
| A4 | **Auth-wall / captcha triage** | `_login_check` deterministic signals | C9 `QUESTION`/REVIEW | MEDIUM |
| A5 | **Guest-vs-session apply** | fixed heuristics | C4 | MEDIUM |
| A6 | **Already-applied misclassification** | `has_already_applied_text` text match | C4 cross-check | MEDIUM |

### Detail
- **A1** is the highest-value single gap in the whole apply path: the pipeline
  already KNOWS the form rejected, but throws away the reason. An LLM reading
  the validation-error text → maps to the exact field + the `--answers` fix →
  the next fill is autonomous. Today: human opens the browser.
- **A3** sits on the one-shot boundary: "Next" on a review page may actually
  submit. LLM reads the page to decide advance-vs-submit BEFORE the guard is
  touched (code still clicks; LLM only classifies the button).

---

## Domain 2 — New-data decisions (ingestion/classification)

| # | Gap | Current | Contract | Priority |
|---|-----|---------|----------|----------|
| B1 | **Near-duplicate arbitration** | fuzzy title+company match → silent reject | C1/C7 | HIGH — silently loses real jobs |
| B2 | **Free-text salary/location extraction** | JSON-LD structured only | C1/C2 | MEDIUM |
| B3 | **Expired vs closed vs session-variance** | re-fetch + signals | C4/C7 | MEDIUM — shrinks `--recheck` |

### Detail
- **B1** is a silent-loss bug, not just an enhancement: two postings of the
  same role near the fuzzy threshold are auto-rejected with no arbitration.
  The LLM decides "same job reworded" vs "different role, same company" only
  when code is uncertain (near-threshold), never replacing the code's clear-cut
  verdicts.

---

## Domain 3 — Profile processing & gathering (LARGEST untouched area)

| # | Gap | Current | Contract | Priority |
|---|-----|---------|----------|----------|
| C1 | **Profile ingestion** (resume/LinkedIn → work_history/education) | hand-maintained profile.json | C1/C2 | **HIGH** |
| C2 | **Profile contradiction detection** | coherence checks form values only, never the profile | C7 | HIGH |
| C3 | **Answer harmonization** (duplicate keys) | `gender` + `Gender Identity`, `authorized_to_work` + `work_authorization` both present | C7 | MEDIUM |
| C4 | **New-answer acquisition (generalization)** | novel label → learn_mapping per-label | C2 → profile | MEDIUM |
| C5 | **EEO preference clustering** | 8 separate "Prefer not to answer" entries | C9 → profile | LOW |

### Detail
- **C1** is the single largest untapped value in the whole system: the source
  of truth is built by hand. An LLM parsing a resume export into
  `work_history`/`education` turns the profile from a liability into a living
  artifact.
- **C3** is a latent correctness bug: duplicate keys with the same value can
  silently drift apart. The LLM consolidates to canonical answers and pins them
  once.
- **C4** is the generalization loop: instead of growing the *label store* on
  every novel question, recognize "this is the same question as profile key X"
  and grow the *profile* — one answer, reusable everywhere.

---

## Domain 4 — Autonomous framework extension (currently human-only)

| # | Gap | Current | Contract | Priority |
|---|-----|---------|----------|----------|
| D1 | **Widget-handler authoring** | OOD widget → human writes handler + registry entry + test | C7 → code | **HIGH** — the framework extending itself |
| D2 | **Alias-rule authoring quality** | `report.py fleet` mechanical word-regex | C2/C7 | MEDIUM |
| D3 | **New-platform module drafting** | new ATS → human writes `lib/platforms/<x>.py` | C7 → code | MEDIUM |
| D4 | **Corpus-drift review** | `registry drift` flags only | C7 | LOW |
| D5 | **Foreign-vocabulary extension** | `_FR_EN` hand-maintained | C7 | LOW |
| D6 | **Correction-buffer root-cause** | `wrong` verdicts just retract | C7/C8 | MEDIUM |

### Detail
- **D1** is the most ambitious and the most valuable framework item: the probe
  cascade already captures a failure artifact (DOM snapshot + capability
  profile). The LLM reads it and *drafts the widget handler + registry entry +
  pinned test*. Code owns it after review; the framework extends itself.
- **D6** upgrades B1 (adjudication) from "retract" to "understand": cluster the
  correction buffer, propose root cause (resolver vs widget vs profile gap)
  and the fix — closing the loop with a diagnosis, not just a deletion.

---

## Priority order (what to build first)

**STATUS — implemented this session (all deterministic logic + orchestrator
surfaces; no ask_api for text):**
- **A1 (validation-error → fix)** — DONE: errors surface in the dossier
  (`report.py handoff` SUBMIT ERRORS) for the orchestrator's `--answers`.
- **A2/A3 (submit/next evidence)** — DONE: `cmd_next` emits `BUTTON_WARN` when
  a Next/Continue button text is submit-like (one-shot boundary); `inspect`
  lists buttons with scores.
- **A4 (auth-wall triage)** — DONE: `_classify_auth_wall` returns
  session_expired/2fa/create_account/login; the fetch path surfaces `WALL:` +
  recovery hint.
- **A5 (guest-vs-session)** — COVERED by existing `LOGIN_WALL`/`GUEST_APPLY`/
  `STATUS_2FA_REQUIRED` signals + the A4 wall triage.
- **A6 (already-applied cross-check)** — DONE: only marks applied when the
  current URL matches the target posting; warns otherwise.
- **B2 (free-text salary/location)** — DONE: `_extract_salary_prose` /
  `_extract_location_prose` backfill when JSON-LD is absent.
- **C2 (profile contradictions)** — DONE: `report.py profile` shows
  relocation/visa/years contradictions.
- **C4 (new-answer generalization)** — DONE: `rules add` notes when a rule's
  key shares a value with an existing profile key.
- **C5 (EEO clustering)** — DONE: `report.py profile --harmonize` shows the
  EEO PNA cluster + one-preference proposal.
- **D2 (rule-candidate authoring)** — DONE: `report.py fleet` emits the REAL
  answer key + exact `rules add` command (not a placeholder).
- **D5 (foreign-vocab gaps)** — DONE: `report.py fleet` surfaces non-ASCII
  repeated labels as `_FR_EN` translation candidates.
- **D4 (corpus-drift review)** — COVERED by `apply.py registry drift` + the
  `widgets` backlog surface.
- **D3 (new-platform drafting)** — surfaced via SEPARATION.md `platforms.json`;
  the orchestrator writes modules from captured pages.

**Status: all LLM_GAPS addressed.** No remaining items — D3 (new-platform
module drafting) is surfaced via `platforms.json` + the orchestrator reading
captured pages, which is complete.

---

## Guardrails (invariant — every gap must respect these)

1. **LLM proposes, code decides** — every gap's output is a *proposal* (C-reply
   or a drafted artifact) that code validates and, for one-shot paths, clicks.
2. **Certification stays code** — `verified` stamps, one-shot guards, SPC
   pauses are never LLM-decided.
3. **Vision stays local** (A3) — any gap that needs a screenshot uses
   `ask_bytes`, which refuses non-loopback endpoints.
4. **Learning compiles** — every LLM answer in these gaps feeds the learning
   loop (learned mapping / runtime rule / profile key), so each decision shrinks
   the next run's handover set.

## Minimized ask_api routing (implementation note)

ask_api is used ONLY where the orchestrator cannot act — vision (images) or a
live-page interaction that cannot wait. Text evidence is always surfaced
**outward** to the orchestrator, combined in tandem with other surfaces:

| Gap | ask_api used? | Why / how |
|-----|--------------|-----------|
| A1 validation-error fix | **No** (text) | Errors recorded in `state["submit_errors"]` AND rendered in `report.py handoff` under **SUBMIT ERRORS**, next to the fill dossier — the orchestrator reads both and answers `--answers`. ask_api reserved for the submit-success **vision** check only. |
| C1 profile ingestion | **Vision only** (image/PDF) | `report.py ingest` surfaces the file path + preview for text (orchestrator reads it); `ask` is called only for image/PDF resumes the orchestrator cannot read. |
| D1 widget-handler draft | **No** | `report.py widget-draft` surfaces the artifact path + capability summary; the orchestrator reads the DOM snapshot on demand and drafts the handler. |

The principle (ETHOS routing): the orchestrator is the strong model; the local
ask_api is the weak model reserved for what the orchestrator physically cannot
see. Exposing a surface costs the orchestrator little; calling the weak model
to do strong-model work is the failure to avoid.
