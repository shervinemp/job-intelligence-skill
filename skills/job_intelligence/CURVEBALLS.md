# Curveball Traces — apply & reach misbehavior scenarios

Verified traces of curveball scenarios that could misbehave. Each traces the
actual code path. Marked: `FOUND` (real bug), `RISK` (latent, needs guard),
`OK` (handled).

## Apply-side curveballs

### C1. `--next` can SUBMIT the form, bypassing all submit guards — FIXED
**Trace**: `_find_next_button` (helpers.py) matches `_NEXT_KEYWORDS_JS` =
`["next","continue","continue to review","review","review application",...]`.
`find_buttons` (page_state.py) scores ANY button whose text contains a keyword,
including the final-step **"Continue"** button on multi-step ATS forms. On the
review page, the last button is frequently "Submit" but on many forms it is
"Continue" or "Continue to Review".
`cmd_next` (inspect.py) clicks `nxt["text"]` with **no `submit_clicked` guard,
no policy check, no domain gate** — `cmd_submit`'s one-shot guard is only in
`cmd_submit`, never in `--next`.

**Result**: running `--next` on a final review page can submit the application
outside the one-shot safety machinery. A duplicate submit, or a submit when the
operator meant to review, is possible.

**Fix (applied)**: `cmd_next` now refuses to click any button whose text is
submit-like ("submit", "send", "apply now", "continue to review",
"continue to submit", "review and submit") — it routes to the gated
`act --submit` path (BUTTON_GATE) instead of clicking. Pinned by tests in
`NextButtonSubmitGate` (test_fill_dispatch.py).

### C2. Stale `--next` after an uncertain submit — FIXED
If a submit's outcome was uncertain (job still `tailored`), `--next` on the
now-submitted page clicks a stray "Continue"/"Next" — possibly a success-page
button or a different flow. `cmd_next` now reads `submit_clicked` from state
and refuses to open the page / click anything if it is set, routing to the
investigation path (GUARD). Pinned by test.

### C3. LinkedIn job with external URL AND Easy Apply — OK
`detect` (detect.py:24-29) returns `external` when an external URL exists,
else `easy_apply`. The ambiguity (both available) resolves to external, which
`navigate` follows. Handled.

### C4. `--fill` on a job already applied — OK
`cmd_fill` (fill.py) checks `stage == "applied"` and returns early with
`emit_status("already applied")`. Handled.

### C5. Shadow re-run on a mid-fill job — OK (mostly)
Shadow is resumable (log-skipped); a job mid-fill that crashed leaves a partial
state but shadow re-runs it from scratch (state is per-job, cleared). Handled.

## Reach-side curveballs

### C6. Connect→DM funnel double-message — OK
`_prior_outreach` excludes the current row, so a connect then a DM on the SAME
person+job is allowed (intended funnel). Cross-job repeats are blocked.
Handled.

### C7. Email after DM to the same person (cross-channel) — HARDENED
`email_sent` and `message_sent` are independent per-channel guards. A person
DM'd on job A could receive an email on job B — `_prior_outreach` blocks if the
IDENTITY matches, but only if a contact row exists with a sent/pending attempt.
If discovery on job B created a NEW row for the same person, `_prior_outreach`
catches it. Handled IF both rows have identity; a person with only a name and no
email/URL on job B is NOT blocked (blank identity = no key). Now surfaced:
`_block_if_prior` prints `BLANK_IDENTITY` at send time whenever the contact has
no identity key, so the operator knows the one-shot guard cannot verify this
send (informational — never hard-blocks, since a name-only connect is
legitimate). Pinned by test.

### C8. `reach.py undo` disarms the cross-person guard — FIXED
`undo` (reach.py) resets `email_sent=0, message_sent=0, reached_out=0` for the
job — so a person previously contacted via that job can be re-contacted after
undo, unless `--confirm` is enforced. The at-risk query previously keyed only
on `contact_attempts` rows, so a send confirmed via `update --set-sent` (flag
set, no attempt row) could be undone WITHOUT `--confirm`, silently disarming
the guard. `cmd_undo` now treats ANY contact with outreach evidence (attempt
row OR `email_sent`/`message_sent`/`reached_out` flag) as at-risk and requires
`--confirm`, naming the people who lose their one-shot protection. Pinned by
test.

## State-machine curveballs

### C9. `advance_job` now validates transitions — OK (fixed this session)
Legal stages enforced; illegal stage raises.

### C10. Applied job with no `applied_at` — OK (fixed + recovered this session)

---

## Recommended fixes (highest value)

1. **C1 (the `--next` submit bypass)** — DONE. `--next` refuses submit-like
   buttons and routes to the gated `act --submit` path. Pinned by tests.
2. **C2** — DONE. `cmd_next` refuses to click when `submit_clicked` is set.
3. **C7/C8** — DONE. Blank-identity sends now surface `BLANK_IDENTITY`; `undo`
   requires `--confirm` whenever ANY outreach evidence exists (attempt row or
   send flag), closing the `update --set-sent` disarm gap.
