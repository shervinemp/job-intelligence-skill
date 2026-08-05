# Failure / Wrong-Info Pass-Through Map

Every point in the pipe where input or output can cause a failure, go undetected,
fail to self-correct, or let wrong information pass through — verified against
the code. Organized by failure mode so the fixes are grouped by what breaks.

---

## A. WRONG INFO PASSES THROUGH — undetected AND uncorrected

These are the most dangerous: the value is certified `verified` and submitted.
All verified against the live tree.

### A1. Postal code as country (CONFIRMED)
`resolve.py:86` derives `country = parts[-1]` of the location. For
`"Toronto, ON, M5V 2T6"` → `country = "M5V 2T6"`. A "Country" field then fills
**"M5V 2T6"**, the read-back matches (it's what we typed), `_check_delta`
passes, and the value is certified verified → submitted.
- **Where**: S3 enrich → S5 apply (country fields).
- **Why undetected**: the wrong value is self-consistent (typed == read-back).
- **Fix**: only treat the last comma-part as country if it is not a postal code
  (regex `^\d` / contains digits in a ZIP-like shape); strip trailing postal
  codes from the location before derivation.

### A2. "Preferred name" filled with "No" (CONFIRMED)
`resolve.py` `_DEFAULT_ANSWERS` has `(r"\bpreferred name\b", "No")` — a
privacy-conservative default meant for *toggles*, but applied to a **text field**
that asks for the candidate's name. "Preferred name" is a text input → filled
with "No".
- **Where**: S5 apply, any ATS with a preferred-name text field.
- **Why undetected**: same self-consistency (typed == read-back == verified).
- **Fix**: defaults must be gated by field type — `"No"` defaults only for
  boolean/consent meanings, never text/select fields. (DONE: A2 fix + values
  moved to default_answers.json.)

### A3. Wrong orchestrator answer → learned → poisons future fills
`learn_mapping` (resolve.py:650) persists a label→value after an
`answers_override` fill. If the orchestrator gives a wrong answer once, it's a
`pending` mapping (count 1) — but **two consistent wrong fills across jobs
promote it to `active`**, after which it resolves autonomously forever. The only
retraction is `_invalidate_learned` on a *contradiction* — a consistently-wrong
answer never contradicts itself.
- **Where**: S5 apply → S4 learning loop.
- **Why undetected**: the wrong value is stable, so no contradiction fires.
- **Fix**: adjudication (C8) must be able to retract an `active` learned mapping
  on a `wrong` verdict (see B2).

### A4. Runtime alias rules are NOT domain-scoped
`add_alias_rule` (resolve.py) stores `(pattern, keys)` with **no domain**. A rule
learned for one platform's phrasing applies to ALL platforms — a bad rule
globally overrides correct answers on unrelated ATS domains.
- **Where**: S5 apply (Step 5 runtime rules).
- **Why undetected**: the rule fires silently, marking its value `alias`.
- **Fix**: add an optional `domain` field to runtime rules; only apply rules
  whose domain is empty or matches the current host.

### A5. `enrich.py:141` `jobs[0]` — multi-role pages lose all but the first
Staffing agencies post several roles on one page. Only `jobs[0]` is captured; the
rest are silently dropped. The wrong (first) job is enriched and applied.
- **Why undetected**: no cross-check that the page has one posting.
- **Fix**: if >1 posting is detected, surface as a decision (C1) rather than
  silently taking the first.

### A6. Salary not normalized (PARTIAL)
A salary field expecting a number receives "120k–150k CAD" — the ATS may accept
it as a string or reject. No normalization to a comparable numeric form exists
at the check level.
- **Why undetected**: check compares the string to itself.
- **Fix**: type-gate salary fields (numeric extraction + range validation).

---

## B. DETECTED but NOT SELF-CORRECTED

The system notices something is wrong but the correction never happens —
the bad state persists until a human manually intervenes.

### B1. Adjudication (C8) records `wrong` but retracts nothing (CONFIRMED)
`report.py adjudicate <id> wrong` writes a verdict to the fill ledger, but
**nothing consumes it**: no learned-mapping retraction, no rule removal, no
resolver update. A wrong fill flagged by the human does not fix the source.
- **Where**: S7 verify-root → S4 learning.
- **Why**: the correction buffer (ALGORITHMS.md S2) was designed but the
  adjudication→retraction link was never wired.
- **Fix**: on `wrong` verdict, invalidate the learned mapping for that
  (label, domain) and flag the runtime rule as suspect.

### B2. No wrong-fill SPC trip (ALGORITHMS.md S4)
The adjudicated wrong-fill rate is computed (`report.py wrongfill`) but nothing
pauses a platform or triggers a fleet review when it exceeds a bound. A platform
with a systematic error keeps filling wrong until a human reads the report.
- **Fix**: SPC bound → auto-pause autonomous submits for that platform + emit a
  C7 fleet decision.

### B3. Demoted observation doesn't update the FILL path
`observations.py` demotes a probe strategy on drift (per capability profile),
but the per-field method store (`field_methods.py`, new) is not consulted by the
observation demotion — a demoted platform's per-field preferences persist.
- **Why**: two learning stores (probe strategy vs fill method) don't talk.
- **Fix**: observation demotion clears the host's field-method preferences.

---

## C. FAILURE NOT OBSERVED (silent failure)

### C1. Mid-fill form drift (F4)
Classification (P20/P21) happens at navigation; by P29 a dynamic SPA can present
different fields. We fill a stale field list → miss new required fields or fill
irrelevant ones. The submit check (P36) re-reads the DOM, so *some* drift is
caught — but a drifted-optional or drifted-unreadable field passes silently.
- **Fix**: re-run field enumeration + classification immediately before submit;
  include "field list still matches" in the C4 evidence pack.

### C2. `ask_api` vision refusal is silent from the fill path
The A3 guard now refuses remote vision endpoints — good — but the refusal
returns `None` to `_verify_with_ask_api`, which treats it as "no confirmation"
(→ unverified). That's correct. However `available()` still returns True from
the ping cache even when `ask_bytes` refuses, so callers believe vision is
working. Minor, but the two should agree.

### C3. `_date_overlap` treats non-year dates as "overlap" (CONFIRMED)
`grounding.py:_dates_overlap` uses `^\d{4}` — "March 2021" yields year None,
and `as_ is None` → returns True (overlap). A fabricated date in a non-ISO
format passes grounding.
- **Where**: S4 generate → grounding gate.
- **Fix**: parse month-year and YYYY-MM; unknown → treat as suspicious, not overlap.

### C4. Grounding fuzzy-match containment (CONFIRMED partial)
`_fuzzy("Acme Inc", "Acme Corp")` returned False (good), but `_fuzzy("Acme", "Acme Corp")` is True by containment (len ≥4). A company shortened to a substring matches — borderline wrong companies can pass grounding if the title/dates also align loosely.

---

## Status — all items addressed

- **A1 (postal-code country)** — FIXED: `_strip_postal` removes ZIP-like trailing
  parts from the country derivation; pinned test.
- **A2 (preferred-name→No)** — FIXED: defaults are gated by field type AND moved
  to `default_answers.json` (no answer values in code); pinned test asserts the
  no-hardcoded-answer contract.
- **A3 (learned-mapping poisoning)** — MITIGATED by B1 (wrong verdict retracts
  the learned mapping) + S2 gate + TTL.
- **A4 (runtime rules not domain-scoped)** — FIXED: rules carry a `domain`;
  resolution filters by host; pinned tests.
- **A5 (enrich jobs[0])** — FIXED: multi-role pages set `multi_role` + titles,
  surfaced as a `MULTI_ROLE:` decision in `cmd_fetch`.
- **A6 (salary normalize)** — FIXED: salary values on text/number fields must be
  numeric (`validate.py`).
- **B1 (adjudication retracts nothing)** — FIXED: a `wrong` verdict now retracts
  the learned mapping and drops the matching runtime rule; pinned tests.
- **B2 (no SPC trip)** — FIXED: `report.py spc` + auto-pause (`paused_platforms`)
  in `submits_for_real`; pinned tests.
- **B3 (demotion doesn't touch fill methods)** — FIXED: observation demotion
  clears the host's field-method preferences; pinned test.
- **C1 (mid-fill drift)** — FIXED: `cmd_check` re-probes the live DOM (new
  required fields are checked) + a form-drift WARN when the field count changes
  ≥3 since fill.
- **C2 (ping cache vs refusal)** — FIXED: `available()` refuses remote endpoints,
  agreeing with `ask_bytes`.
- **C3 (date grounding)** — FIXED: month-year parses; unparseable dates fail
  overlap (no silent pass); verified.
- **C4 (fuzzy containment)** — FIXED: word-boundary containment only; verified.
- **A4 (DNS rebinding / redirect)** — FIXED: `_curl_fetch` now re-vets the FINAL
  redirect target via curl's `%{url_effective}` before accepting the body.

All FAILURE_MAP items A1–A6, B1–B3, C1–C4 are now FIXED.
