# Runbook: shadow → review → enable mappings → gate

How to roll out the ADR-001 automation features safely, in order. Each step is
reversible and gated; nothing below changes live behavior until you opt in.

## 0. Where the knobs live

All flags are in `apply_policy.json` in `JI_HOME` (default `~/.ji/`). Precedence:
built-in defaults ← `apply_policy.json` ← `JI_APPLY_MODE` env ← `act --shadow` (per run).

```jsonc
// ~/.ji/apply_policy.json — every field optional; shown with its default
{
  "mode": "live",              // live | shadow | hold
  "paused": false,             // kill-switch: block ALL submits
  "use_mappings": false,       // Phase 3: consult/learn the mapping store
  "enforce_validation": false, // Phase 4: skip (escalate) values failing validation
  "gate_submit": false,        // Phase 4: hold submit if the job has invalid fields
  "never_auto": ["freetext"],  // categories never cached as mappings
  "ttl_days": 90
}
```

Audit logs: `~/.ji/results/<jid>/apply_audit.jsonl`. Screenshots: `~/.ji/screenshots/`.

## 1. Shadow run (observe, never submit)

```bash
export JI_APPLY_MODE=shadow          # or per run: act ... --shadow
python apply.py detect <jid>
python apply.py act --fill <jid>     # fills, screenshots, writes audit; never submits
python apply.py act --next <jid>     # repeat fill/next for multi-page
python apply.py act --submit <jid> --confirm   # blocked in shadow; logs intended submit
```

Run a representative batch (different ATSs: Greenhouse, Lever, Ashby, Workday,
LinkedIn Easy Apply). Each `act --fill` prints a `STATUS: audit ...` line and
appends to the audit log.

## 2. Review the audit data

```bash
cat ~/.ji/results/<jid>/apply_audit.jsonl
```

Each `field` line: `label, value, provenance, category, filled, validated`. Read for:

- **Provenance mix** — `user_typed` (you/LLM supplied) vs `ephemeral` (profile fact)
  vs `no_match`. A high `no_match` rate is the gap a richer profile / mappings fill.
- **`invalid` count** (in the `STATUS: audit` summary) — values that didn't fit the
  live field. Investigate before trusting `enforce_validation` / `gate_submit`.
- **Categories** — how often `legal`/`salary`/`eeo` actually appear; confirms what
  must stay human-gated.

This is the evidence base for the remaining toggles. Do not enable them blind.

## 3. Enable validation enforcement + submit gate (Phase 4)

Once the audit shows `validated` is reliable (few false negatives):

```jsonc
{ "enforce_validation": true, "gate_submit": true }
```

- `enforce_validation`: a resolved value that fails `validate_value` is **escalated**
  (left unfilled) instead of filled — prevents "advance on an unvalidated guess".
- `gate_submit`: `act --submit` **holds** (does not click) if the job has any invalid
  field. Combine with `mode: live` to actually submit clean jobs.

`paused: true` is the emergency stop — blocks every submit regardless of mode.

## 4. Enable the mapping store (Phase 3)

When you want cross-job learning of answers to recurring questions:

```jsonc
{ "use_mappings": true }
```

- During fill, fields the profile can't resolve are answered from **confirmed**
  mappings (value recomputed from your profile, validated against the live field).
- `--answers` you supply are recorded as **pending** mappings.
- On a verified submission, only **corrected-then-passed** pending mappings auto-promote
  (one-shot guesses are not trusted).

Review and promote manually after a shadow run:

```bash
python apply.py mappings list <jid>      # pending mappings for the job
python apply.py mappings confirm <jid>   # promote them (human-reviewed)
python apply.py mappings clear <jid>     # discard them
```

Stores: `~/.ji/mappings.json` (confirmed), `~/.ji/mappings_pending.json` (pending).
Profile edits invalidate affected mappings automatically (bump `profile._version`).

## 5. Go live

With confidence from steps 1–4: `mode: live`, `use_mappings: true`,
`enforce_validation: true`, `gate_submit: true`. The pipeline now auto-fills from
profile + confirmed mappings, validates every value, holds anything invalid, and
submits clean jobs — with `paused` as the kill-switch and the audit log as the record.

## Rollback

Any step: set the flag back to its default (or `mode: shadow`, or `paused: true`).
No data migration — the stores and audit logs are additive and ignored when off.
