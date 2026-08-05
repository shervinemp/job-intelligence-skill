# Static Points & The Self-Adaptation Meta-Flow

Two analyses:
1. **Part A** — every static/rigid point in the pipeline that cannot adapt to
   new input, with evidence and the gap it creates.
2. **Part B** — the **meta-flow**: the subsystem that builds and adapts the flow
   itself (the probe cascade, observation learning, resolution learning, drift
   demotion, and the human/orchestrator loops that update static vocab).

---

## Part A — Static points that cannot adapt

The pipeline has exactly **one** adaptive layer (resolution + widget strategy,
see Part B) and everything else is fixed. Every fixed structure is a place where
novel input falls through.

### A1. Fixed answer vocabulary (`apply/common/resolve.py`)

| Structure | What it is | Gap |
|-----------|-----------|-----|
| `_ALIAS_RULES` | ~50 curated regex→key rules | A label that doesn't match any pattern → `no_match`. Only extends via hand-editing + the orchestrator's `--answers` (which `learn_mapping` then persists — the ONE closed loop). |
| `_DEFAULT_ANSWERS` | conservative form defaults | A novel consent/opt-in phrasing not in the list gets no default. |
| `_ATTR_MAP` | name/id → semantic key | ATS vendors using novel attribute names fall through to label matching. |

### A2. Fixed country set (`apply/common/match.py`)

`COUNTRY_ISO` = **32 entries**. A "phone country code" intl-tel picker for
Qatar, Uruguay, or any country outside the 32 cannot be verified via the flag
class — `country_iso()` returns `""` and verification falls back to
fragment-only (unverifiable). **Static by design, but it means the verification
net has holes for ~150 countries.**

### A3. Fixed classifier keyword lists

| Structure | Location | Gap |
|-----------|----------|-----|
| `_HANDOVER_KW` | `lib/report.py:468` | A personal question phrased outside the ~20 keywords (e.g. "Will you now or in the future require sponsorship?") is misclassified as DATA instead of USER in the owner-split. |
| `_RISK_KEYWORDS` (new) | `apply/common/terms.py` | Same shape — a risk field phrased outside the list gets flag-not-block. |
| `_SKIP_DOMAINS` | `extract.py` | **Noise filter, not a security control** — a new job-source domain isn't skipped until hand-edited. |

### A4. Fixed structural assumptions

| Assumption | Location | Gap |
|-----------|----------|-----|
| `jobs[0]` | `enrich.py:141` | Staffing agencies posting MULTIPLE roles on one page: only the first is captured. |
| `parts[-1]` = country | `resolve.py:86` | "Ottawa, ON K1A 0B1" → country becomes "K1A 0B1". A postal code in the location breaks country derivation. |
| salary as one string | `enrich.py` JSON-LD | "120k–150k CAD" vs "£80k" vs hourly — no normalization to a comparable form. |
| `^\d{4}` date | `grounding.py:_dates_overlap` | "March 2021" dates won't match a year-range overlap. |
| fixed categories | `categories.json` | Only `tech`/`general`; a new category requires hand-editing + admission re-routing. |

### A5. The structural gap summary

```
adaptive:   resolve (learned mappings) ──► fills novel labels after orchestrator answers
            probe cascade (observations) ──► learns widget strategy per capability
rigid:      aliases, defaults, attr-map, countries, handover-kw, risk-kw, skip-domains,
            jobs[0], country derivation, salary/date formats, categories
```

**The failure mode**: novel input isn't refused loudly — it silently falls to
`no_match` / `unverifiable` / "wrong location" and waits for a human. There is no
loop that converts a *repeated* miss into a *vocabulary update* (that's exactly
the S2/S3 correction loop from ALGORITHMS.md — proposed, not yet wired).

---

## Part B — The self-adaptation meta-flow

This is the flow that **builds the flow**: how the pipeline learns widget
strategies, learns answers, detects drift, and adapts. It is the one place the
system changes itself.

### The four adaptation loops

**Loop 1 — widget-strategy learning (probe cascade → observation → promote).**
- `apply/common/observations.py` is the brain: it records, per **capability
  profile** (not per domain), which probe strategy won and which failed.
- `record_success`: same winning strategy N times (`CONFIRM_THRESHOLD`) →
  `confirmed=True` → tried first next run.
- `record_failure`: a confirmed strategy returns 0 fields → `fail_count`++;
  at `DRIFT_FULL_DEMOTE_THRESHOLD` (2) → `confirmed=False`, strategy reset,
  candidate list cleared → next cycle re-confirms from scratch.
- `recommend_start_strategy`: confirmed > candidate > default.
- Managed by `apply.py registry` (`candidates/confirm/clear/corpus/failures/drift`)
  + `apply/common/registry_cli.py`.

**Loop 2 — answer learning (resolver).**
- `resolve.py:learn_mapping` persists a label→value mapping after an
  `answers_override` (orchestrator-supplied) answer, **domain-scoped**, with a
  consistency threshold before it resolves autonomously.
- `_invalidate_learned` fires when an explicit answer CONTRADICTS a learned
  mapping — the only built-in retraction path.

**Loop 3 — drift detection (corpus + registry drift).**
- `apply/common/corpus.py` captures DOM snapshots keyed by capability profile;
  `apply.py registry drift` re-probes them to detect silent ATS redesigns and
  auto-demotes stale observations.

**Loop 4 — the human/orchestrator loops.**
- `registry confirm/clear` — manual promote/demote of observations.
- `--answers` → learn_mapping — orchestrator extends the answer vocabulary.
- The change protocol (SKILL.md): edit → `lint.py` → pytest → shadow → verify →
  **manual sync to the repo tree** → repo pytest → commit. This is how *static*
  vocab (aliases, countries, keywords) gets extended — it's a human process, not
  a pipeline loop.

### The meta-flow diagram (what this section maps)

```
            ┌──────────────────────────────────────────────────────────────┐
            │                    ADAPTATION META-FLOW                      │
            └──────────────────────────────────────────────────────────────┘

 probe cascade runs ──► capability profile ──► record_success/failure
        │                                          │
        ▼                                          ▼
 strategy wins N× ──► confirmed=True ──► tried first next run
        │
        ▼   strategy returns 0 fields (drift)
 record_failure ──► fail_count ──► ≥2 ──► demote, reset, re-confirm from scratch
        │                                          ▲
        ▼                                          │
 corpus snapshot ──► registry drift ──► re-probe ──┘ (silent ATS redesign)

 answer resolution:
   resolver ──► no_match ──► orchestrator --answers ──► learn_mapping ──► resolves next run
        │                                          │
        └────────── answers_override CONTRADICTS ──┘ ──► _invalidate_learned

 static-vocab extension (aliases/countries/keywords/categories):
   NOT a pipeline loop — a human process: edit → lint → pytest → shadow → sync → commit
```

### What the meta-flow is missing (the gaps that matter)

1. ~~No loop converts repeated misses into vocabulary updates.~~ **WIRED.** A
   repeated `no_match` label now resolves at runtime via
   `report.py rules add "<regex>" "<answer_key>"` — `apply/common/resolve.py`
   loads a `state/alias_rules.json` runtime store in Step 5 (before the
   static `_ALIAS_RULES`), so a confirmed orchestrator answer outranks the
   source defaults without a deploy. `report.py fleet` emits the exact
   `PROMOTE:` command. `report.py rules list|add|promote|clear` manages the
   store; `promote` is S2-gated (a learned mapping needs ≥2 confirms before
   it can become a rule); rules carry a `last_seen` TTL and are reaped if
   never matched in `_RULE_TTL_DAYS`.
2. **Classifier keyword lists are now data-driven.** `report.py keywords
   list|add` extends the risk/handover keyword lists at runtime (no code
   edit) — `apply/common/terms.py` merges static + runtime keywords in
   `is_risk_field`, and `report.py`'s handover owner-split uses
   `_handover_kw()`.
3. **Observation learning is per-capability-profile AND now per-field.**
   **DONE.** `apply/common/field_methods.py` learns label → proven filler
   method AND label → proven verification strategy, scoped by host, S2-gated
   (≥2 confirms), consulted first in `_fill_one` / the combobox reader. This
   covers the Antigua method class ("this platform's country picker is a
   combobox") AND the verification-strategy class (flag-class vs text
   read-back) — the deepest gap is closed.
4. **The meta-flow's runtime-vocabulary loop now has a contract.** The
   `report.py rules`/`keywords` surfaces ARE the C7/S2 wiring. It is invoked
   manually — fully automating "repeated miss → auto-add rule" still needs
   the S2 correction-buffer confidence threshold (≥2 same-domain wrong
   verdicts) before auto-promotion is safe; `promote` already enforces the
   ≥2-confirm half of that gate.
5. **Static vocab now has expiry for runtime rules** (rule TTL + dead-owner
   lock reaping); static source rules still have no expiry, which is
   acceptable — they are reviewed in the change protocol.

### Recommendation

Wire Loop 4 into the meta-flow with a real contract: when `report.py fleet`
detects a repeated `no_match` label or a demoted observation, emit a
`C7 DECISION` / `S2 correction` the orchestrator resolves — and the resolution
compiles back into the *static* vocab (aliases, countries, keywords) via the
same `learn_mapping`-style promotion, not into a throwaway per-run answer.
