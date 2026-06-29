# ADR-001: Auto-fill memory for unattended applications

**Status:** Phase 1 implemented; Phase 2 partial; Phases 3–4 deliberately deferred
**Date:** 2026-06-29
**Context owner:** apply pipeline (`apply/`)

> **Phases 3–4 are intentionally not started.** The mapping store (3) and gating
> enforcement (4) depend on the Phase-1 shadow-run data to be built correctly —
> e.g. how often each tier/provenance actually occurs, and how often single-pass
> values would have been wrong. Building them before that data exists would be the
> speculative over-engineering this ADR exists to avoid. Run shadow mode on real
> jobs first, read the audit logs, then design 3–4 against the evidence.

## Context

The apply pipeline is moving toward **full automation** — running unattended, submitting
applications without a human eyeballing each form. The repo previously stubbed a
`label → answer` value cache (`session_cache.json` + `label_map.json`, promote-after-two-
encounters). It was never wired up (nothing wrote the caches; `promote_session_cache()`
always returned 0) and was removed in the cleanup preceding this ADR — see the audit notes
in the commit that deleted `apply/common/answer_matcher.py`.

The question this ADR answers: **for unattended submission, what cross-run memory should the
pipeline have, and how do we make it safe?**

## Decision

1. **Do not rebuild a `label → value` answer cache.** It optimizes the cheap, safe part
   (typing a known fact) while creating silent-wrong-submission risk on the expensive part
   (judgment questions submitted forever across every matching job).
2. **Cache the field→meaning *mapping*, never the value.** A mapping points at a profile key
   + transform; the value is computed fresh from the profile on every fill. Updating the
   profile propagates everywhere with zero stale entries.
3. **Separate data into three tiers by caching semantics** (below).
4. **The real deliverable is the safety substrate** — validation, gating, audit, shadow mode
   — not the cache. Build that first.

### Three tiers

| Tier | Examples | Caching rule |
|------|----------|--------------|
| **1. Deterministic facts** | name, contact, links, work-authorization-by-country, sponsorship, years-by-skill, salary band, notice period | Live in versioned `profile.json` (knowledge base). Resolved deterministically. The *mapping* label→key may be cached; the value never is. |
| **2. Per-job generated content** | cover letter, "why this company", role-specific prose | **Never cached.** Always pulled from `results/<jid>/`. Cross-job reuse is a bug. |
| **3. Novel screener questions** | weird custom attestations, one-off prompts | **Escalate, don't guess.** LLM proposes; first encounter is filled-and-held or policy-gated. Human/policy confirmation closes the loop and promotes the *mapping*. |

## Data schemas

### Profile knowledge base (`profile.json`, Tier 1)

Structured facts + parameterized resolvers. Values single-sourced here.

```jsonc
{
  "first_name": "…", "last_name": "…", "email": "…", "phone": "…",
  "location": "Ottawa, ON, Canada",
  "links": { "linkedin": "…", "github": "…", "portfolio": "…" },
  "work_authorization": { "CA": true, "US": false },        // by ISO country
  "requires_sponsorship": { "CA": false, "US": true },
  "experience_years": { "python": 5, "java": 2, "management": 1 },
  "salary": { "currency": "CAD", "min": 85000, "target": 95000 },
  "notice_period_weeks": 2,
  "answers": { /* exact static answers, normalized-key → value */ },
  "_version": 7                                             // bumped on any edit; invalidates mappings
}
```

### Mapping store (`mappings.json` — cache the mapping, not the value)

```jsonc
{
  "<fingerprint>": {
    "maps_to": "work_authorization",        // profile key / resolver name
    "args": { "country": "CA" },            // extracted from question context
    "transform": "yesno",                   // yesno | currency | int | passthrough | option_match
    "category": "legal",                    // generic | legal | salary | eeo | freetext
    "source": "llm",                        // profile | llm | human
    "confidence": 0.82,
    "profile_version": 7,                   // version this mapping was validated against
    "options_hash": "ab12…",                // hash of the field's option set; drift ⇒ invalidate
    "platform": "greenhouse",
    "hit_count": 4,
    "created_at": "2026-06-29",
    "last_confirmed": "2026-06-29"          // only set on verified submit
  }
}
```

**Fingerprint** = hash of `normalize(question) + sorted(options) + field_type + platform +
section_context`. Two questions with the same words but different option sets are different
keys.

### Audit log (`results/<jid>/apply_audit.jsonl`, one line per field)

```jsonc
{ "ts": "…", "label": "…", "resolved_value": "Yes", "provenance": "ephemeral|user_typed|mapping|llm",
  "confidence": 0.82, "category": "legal", "validated": true, "submitted": false, "held_reason": null }
```

### Policy (`apply_policy.json`)

```jsonc
{
  "mode": "shadow",                 // shadow | hold | live
  "auto_submit_min_confidence": 0.9,
  "never_auto": ["freetext"],       // attestations DO auto-click (operator owns it);
                                     // EEO stays deferred only because the code already
                                     // declines to auto-fill it — leave that behavior as-is.
  "max_llm_fields_per_job": 8,      // exceed ⇒ escalate whole job
  "ttl_days": 90,
  "paused": false                   // kill-switch
}
```

## Reliability requirements (non-negotiable for unattended submit)

1. **Semantic keys**, not raw labels (fingerprint above).
2. **Fill-time validation against the live field.** Resolved value must match an available
   option or pass a format check. No match ⇒ unmapped ⇒ escalate. Never submit a
   non-conforming value.
3. **Provenance + versioning + TTL.** Invalidate mappings on `profile._version` change or
   after `ttl_days`.
4. **Confidence + category gating.** Facts auto-submit; LLM-mapped novel questions auto-fill
   but hold, or auto-submit only if `confidence ≥ threshold AND category ∉ never_auto`.
5. **Shadow mode + audit log.** Fill + screenshot + log intended submission *without
   clicking submit*, build a track record, then flip to `live`. Audit log is the diagnostic
   record of what was answered and why.
6. **Negative caching / kill-switch.** Remember "couldn't safely answer"; honor `paused`.
7. **Concurrency-safe persistence.** Atomic writes + file lock (parallel job runs).
8. **Verified submission is necessary but NOT sufficient for promotion.** "The ATS accepted
   it" ≠ "the answer was correct" — fill-time validation only catches form-rejectable errors
   (format, required, option-mismatch), never semantically-wrong-but-valid answers ("No" for
   work authorization). Promotion confidence ladder, highest first:
   1. **Trusted fact** (profile / `decisions.md`) — deterministic.
   2. **Corrected-then-passed** — the form rejected a first value and the revised value passed;
      strong evidence the mapping is right (the form taught us, the LLM converged).
   3. **Reviewed in shadow/hold** — the only thing that catches the wrong-but-valid class.
   4. **One-shot pass + submitted** — weakest; **do not auto-promote a mapping on this alone.**
9. **Advance/submit only on a validated-or-trusted value.** Never push an uncertain first guess
   forward — once a wrong-but-valid answer is submitted it is unrecoverable. Surface the
   uncertainty and let the intra-page correction loop (fill → validate → revise) run *before*
   committing. The audit log must capture the full correction trajectory; those correction
   events are the prime training signal for reliable mappings.

## Phased delivery

Each phase is independently useful and de-risks the next.

- **Phase 1 — Observability (no submission risk). [IMPLEMENTED]** `apply/common/policy.py`
  (mode live/shadow/hold, default live) + `apply/common/audit.py` (per-job
  `results/<jid>/apply_audit.jsonl`: field value + provenance + tier category + filled). In
  shadow/hold, `act --fill`/`act --submit` fill + screenshot + log but never click submit.
  Verify hardened: confirmation-URL signal added; vision is a last-resort fallback gated on
  `ask_api.available()`. Enable via `JI_APPLY_MODE=shadow`, `apply_policy.json`, or `act --shadow`.
  *Next: run across real jobs and read the audit logs to size Phases 2–4.*
- **Phase 2 — Structured profile + fill-time validation. [PARTIAL]** Done: `apply/common/
  validate.py` (option-match + email/phone/number/url checks) wired into the audit pass
  (records `validated`, surfaced as `invalid=N` in the fill summary); reconciled
  resolve._PROFILE_KEYS with act._KNOWN_PROFILE_KEYS (string-valued facts now resolve, with
  str-coercion and explicit-key-wins location derivation); removed the vestigial `ca`
  threading and a dead detect.py branch. Deferred: *enforcing* validation at fill time
  (escalate-on-invalid) — gated on shadow-run data confirming it doesn't break working fills;
  and wiring `decisions.md` → structured facts (needs the mapping/LLM layer). Booleans
  (authorized_to_work, requires_sponsorship) still resolve via Phase 3, not here.
- **Phase 3 — Mapping store.** Fingerprinted label→key mappings, provenance + TTL, promoted
  only on verified submit. Cross-run learning that shrinks the escalation set.
- **Phase 4 — Policy/gating.** Confidence thresholds, sensitive-category carve-outs, hold vs.
  live, kill-switch.

## Consequences

- **Pro:** facts single-sourced; profile edits propagate instantly; no stale-value risk; every
  submission auditable; sensitive questions never auto-answered; learning shrinks the manual
  set over time.
- **Con:** more moving parts than a value cache; requires the policy/audit scaffolding before
  any autonomy is safe. That cost is the point — it is the price of unattended submission.
- **Non-goal:** pure autonomy. CAPTCHA/login/2FA force a human-handoff fraction, so the system
  is **human-in-the-loop-on-exceptions**. The memory layer's only job is to shrink that set.

## Design rule of thumb

> Cache mappings, not answers. Single-source facts. Generate per-job content per job.
> Escalate novel judgment. Never auto-submit a value you didn't validate against the live field.

## Related automation decisions (session 2026-06-29)

Scoped during the automation-direction review. Recorded here so they're not re-litigated.

- **Tests:** real `pytest` over pure logic (`resolve`, `ButtonIntentClassifier`, step-regex,
  `normalize`) + an import smoke. **Not** CLI/`--dry-run` invocations. Add to CI.
- **`--dry-run` stays** as an operator/shadow preview primitive (no DOM, resolve-only). It is
  not a test mechanism. Keep its help footprint minimal.
- **Verify hardening:** detection order is (1) DOM text + URL transition / confirmation
  element from the HTML dump, (2) form-absence heuristic, (3) vision check **only** as a last
  resort and only when `ask_api.available()`. Vision is never the primary signal (model not
  guaranteed served). Screenshot becomes an audit artifact, not a dependency.
- **Fit gate:** the intake gate (`extract.py admit/reject` + `decisions.md` non-fit rules)
  already exists and is intentionally lenient ("don't self-reject"). Add a fit **score for
  prioritization/ordering only** (apply best-fit first under rate/volume caps); do **not**
  add score-based self-rejection.
- **Attestations / signatures:** auto-click. The operator owns responsibility. Not a hard-stop.
- **Resumability:** not building a partial-submit state machine. ATS flows don't commit until
  the final submit, so recovery = re-run from the top (fill already skips pre-filled fields).
  The only safeguard needed is idempotent submit detection (don't re-fire a submit that
  already landed) — covered by verify hardening above.
