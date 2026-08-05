# Trace comparison — observability/control today vs after C1+C2+C3

One realistic run: a tailored job `abcd1234ef567890` on `jobs.acme.com` (Workday-style
ATS with a login wall, then a phone-country `<select>`), submit, then a LinkedIn DM to a
recruiter. The left column is the **actual** stderr/dossier trail today (verbatim signal
strings from the code). The right column is what the orchestrator sees **after** C1
(auth-wall module), C2 (field-fill module), C3 (outreach ledger) — assuming the
evidence-preservation contract is honored.

Legend: `→` = same signal survives; `NEW` = signal the deepening *adds*; `⛔` = signal a
sloppy deepening would swallow (the regression we must prevent).

---

## Act 1 — auth wall

### Today (fill.py `_handle_login_wall`, inline in the orchestrator)

```
LOGIN_WALL: jobs.acme.com
  Switched to Sign In form
  Auto-login: me@x.com (2 password(s))
  LOGIN: OK with password #2
LOGIN: promoted this password to primary for jobs.acme.com
```

### After C1 (auth-wall module)

```
LOGIN_WALL: jobs.acme.com
  Switched to Sign In form
  Auto-login: me@x.com (2 password(s))
  LOGIN: OK with password #2
LOGIN: promoted this password to primary for jobs.acme.com
```

**Identical.** C1 carries the existing trail verbatim — it only relocates the code, not
the signals. The orchestrator can still see *which* password won, and that promotion
happened, which is exactly C-O1/C-O3 (step-level + causal trace).

### The regression a sloppy C1 must not do

```
LOGIN_WALL: jobs.acme.com
  STATUS: login_ok
```
⛔ C-O1 violated: the orchestrator can no longer tell *password #2 won* from *assumed OK
on uncertain* — two very different trust levels.

---

## Act 2 — phone-country `<select>` (the Antigua path)

### Today (fill.py → fill_runner → resolve → select strategy → read-back)

```
  SELECT: ans='+1' country_words=['canada'] options=249
    +1/CAN: 'Canada (+1)'
    COUNTRY-MATCH: 'Canada (+1)'
  delta: 'CA' (verified) method=native_setter
STATUS: filled 3 fields, 0 REQUIRED unanswered
NEXT: check
```

The orchestrator sees the *decision*: country known, matched by name, not bare code.
This is the whole Antigua safety story, visible live.

### After C2 (field-fill module, evidence-preserving)

```
  SELECT: ans='+1' country_words=['canada'] options=249
    +1/CAN: 'Canada (+1)'
    COUNTRY-MATCH: 'Canada (+1)'
  delta: 'CA' (verified) method=native_setter
STATUS: filled 3 fields, 0 REQUIRED unanswered
NEXT: check
```

**Identical, but now with a richer dossier record underneath:** C2 returns
`{method, provenance, delta, strategies_attempted: [select_option→native_setter],
read_back:'CA'}` and that record *feeds* `handoff.json`. The stderr trail is unchanged;
the dossier gains causality (why native_setter won) that `ji diff`/`ji verify` can show.

### The regression a sloppy C2 must not do

```
  delta: 'CA' (verified) method=native_setter
STATUS: filled 3 fields
```
⛔ C-O4: the per-strategy fallback (`select_option` tried, failed, → `native_setter`)
vanishes. If the field is later rejected, the orchestrator can't tell "DOM lazy" from
"value≠text" from "React not notified" — three different fixes.

---

## Act 3 — submit + uncertain outcome

### Today (submit.py `_determine_outcome`)

```
  OUTCOME: submitted — form gone, success text found
STATUS: submitted
```
or the uncertain case:
```
  OUTCOME: uncertain — no success signal, form still present
STATUS: submitted (uncertain outcome — Review recommended)
NEXT: investigate
```

### After C2/C1 — no change on this path

C2 and C1 do not touch submit; C5 (rejected) would have. The one-shot guard stays spread
across jid_lock + policy + domain_gate — and the phantom `jobs.state` read at
report.py:149 is deleted (one-line fix, C5's only worthwhile part).

---

## Act 4 — LinkedIn DM

### Today (reach.py cmd_message, inline choreography)

```
MESSAGE_SENT: Keyvan K
```
or, on a repeat:
```
ALREADY_REACHED: Keyvan K was already contacted for job abcd1234 (linkedin_message/sent)
  — one-shot guard. Use --force to override.
```

### After C3 (outreach ledger) — **IMPLEMENTED**

```
MESSAGE_SENT: Keyvan K
```
The stderr trail is byte-identical (C3 relocated the writes to
`lib/outreach_ledger.py`, not the signals). What changed is *where* the writes
live and their atomicity: flag + attempt + event now commit once on one
connection, so a mid-write failure rolls back cleanly (grilling item J) instead
of leaving `message_sent=1` with no attempt row. Guard path unchanged:
```
ALREADY_REACHED: Keyvan K was already contacted for job abcd1234 (linkedin_message/sent)
  — one-shot guard. Use --force to override.
```
Uncertain path unchanged: `MESSAGE_UNCERTAIN:` still surfaces, and
`update --set-sent` still settles via the ledger (`settle()`, which also handles
the flag-only case — item K/C8). `undo` now reads its at-risk gate from
`outreach_at_risk()` (flag + attempt evidence, C8).

### The regression a sloppy C3 must not do

```
MESSAGE_SENT: Keyvan K
```
...where "sent" actually means "transmitted, outcome uncertain". Today the *uncertain*
path already prints `MESSAGE_UNCERTAIN: ... --set-sent` — C3 must keep that as a distinct
first-class return, not collapse it into `MESSAGE_SENT`. If it did, C-O2 is violated: the
orchestrator would believe a message landed and never settle it, and the "never silently
resend" guarantee is broken by *observation failure*, not logic failure.

---

## Side-by-side summary

| Stage | Today | After C1+C2+C3 | Regression to prevent |
|-------|-------|----------------|----------------------|
| Auth wall | full step trail | **identical** | status enum only |
| Field fill | strategy trail live | **identical** stderr + richer dossier | collapse to `{method,verified}` |
| Submit | outcome cascade | unchanged (C5 skipped) | — |
| Outreach | sent/guard/uncertain | sent + attempt id (`NEW`); guard/uncertain unchanged | collapse uncertain into sent |

## The invariant that makes all three safe

> **Collapse implementation, never the evidence, never swallow a knob.**

Concretely: every stderr line the orchestrator reads today must still be emitted (C1/C2
relocate, not remove), and every module returns a record *at least as detailed* as the
trail it replaces (C2's dossier-grade struct, C3's attempt-id + uncertain branch). The
three deepenings are safe iff that contract holds; C5 is dropped because its spread is
the redundancy that keeps the guard observable-by-construction.
