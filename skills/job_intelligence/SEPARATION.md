# Separation of Concerns — Profile, Framework, Platform

Where every hardcoded value lives, what belongs in data vs code, and how to
abstract platform-dependent behavior. The goal: **user data and preferences in
data files, framework logic in code, platform specifics behind an abstraction** —
so the pipeline can be re-skinned (new user, new ATS ecosystem) without code
changes.

---

## The three layers

```
┌─────────────────────────────────────────────────────────────────────┐
│  USER  (profile.json + preferences)  — all values, zero logic       │
│   contact facts · work_history · education · answers (label→value)  │
│   risk/consent/relocation preferences                               │
└─────────────────────────────────────────────────────────────────────┘
              ▲ resolve() reads facts; never writes values here
┌─────────────────────────────────────────────────────────────────────┐
│  FRAMEWORK (the deterministic core)  — all logic, zero user data     │
│   resolver chain · verify semantics · one-shot guards · check gates │
│   SPC · adjudication · learning loop · CLI dispatch                 │
└─────────────────────────────────────────────────────────────────────┘
              ▲ platform modules register behaviors, never hardcode
┌─────────────────────────────────────────────────────────────────────┐
│  PLATFORM (per-ATS abstractions)  — domain/behavior, zero policy     │
│   registry (URL→capability) · widget handlers · session allowlist    │
│   extraction (lib/platforms/*) · message composition (LinkedIn)     │
└─────────────────────────────────────────────────────────────────────┘
```

**The contract**: code never contains user values or platform decisions it
couldn't take to a different user/ecosystem unchanged.

---

## Layer 1 — USER (belongs in data, never code)

| Item | Current state | Action |
|------|--------------|--------|
| Contact facts (name/email/phone/location/zip) | `profile.json` top-level | **DONE** — already data. |
| Work history + education | `profile.json` | **DONE** — already data. |
| Answers (label→value, incl. EEO/sponsorship/salary) | `profile.json` `answers` | **DONE** — already data. |
| Conservative consent defaults | was hardcoded `_DEFAULT_ANSWERS` | **FIXED** — moved to `default_answers.json`. |
| Pronouns → "Yes" mapping (resolve.py:337) | hardcoded `gmap` + `"Yes"` | **HARDCODED** — move pronoun derivation to data (`pronouns.json` or profile answers). |
| `"true"` consent auto-answer (fill_runner:512, gated by `JI_AUTO_CONSENT`) | hardcoded | **HARDCODED** — the *answer* "true" is a value; gate stays, value should come from profile/consent data. |
| Category → gem mapping | `categories.json` | **DONE** — already data. |

**Rule**: any string that is "Shervin's answer" or "this user's preference"
belongs in profile.json / a data file. If you can imagine another user answering
differently, it is not code.

---

## Layer 2 — FRAMEWORK (belongs in code, never data)

| Concern | Location | Why it's code |
|---------|----------|---------------|
| Resolver chain + i18n vocabulary (`_FR_EN`, aliases) | resolve.py | Logic: order, precedence, fail-closed. |
| Verify semantics (echo/containment/bare-code) | filler.py / value_reader.py | The epistemic contract — must be deterministic + tested. |
| One-shot guards, SPC trip, adjudication→retraction | submit.py / fills.py | Safety invariants — must be code + pinned tests. |
| `yes/no/true/false` boolean normalization | match.py / filler.py / coherence.py | **Framework logic, NOT user values** — "yes"→checked is a control-semantics rule every form shares. Keep. |
| Country → ISO (COUNTRY_ISO, 32 entries) | match.py | **Borderline** — it is *reference data*, but small + universal; acceptable in code, better in `data/countries.json`. |
| Stopwords / ALIASES (Male→He/Him etc.) | match.py | **Borderline** — semantic aliases are language-referent data; move to data for augmentation. |
| `_RISK_KEYWORDS` / `_HANDOVER_KW` | terms.py / report.py | **FIXED** — runtime-extendable via `keywords`; static seed remains (policy, acceptable). |
| Platform session allowlist | url_safety.py | **PLATFORM** (see Layer 3). |

**Rule**: boolean normalization ("yes"→checked), safety invariants, and the
verify contract are framework. They are the same for every user and every ATS —
moving them to data would make the safety layer configurable = weaker.

---

## Layer 3 — PLATFORM (behind an abstraction)

Platform specifics are currently **scattered** across 8 files. They must move
behind the existing `lib/platforms/` abstraction + a new registry.

### 3a. The domain scatter (what to consolidate)

| File | Platform hardcode |
|------|-------------------|
| `url_safety.py:37-46` | session allowlist: linkedin, workday, greenhouse, lever, ashby, icims, bamboo, jobvite, comeet, smartrecruiters, workable, breezy, successfactors, taleo, adp |
| `credentials.py:62-67` | icims, greenhouse, lever, jobvite domains |
| `extract.py:50-51` | `_SKIP_DOMAINS`: linkedin.com/comm, /feed, ... |
| `detect.py:25-28` | `"linkedin.com/jobs"` URL classification |
| `check.py:115`, `fill.py:434`, `submit.py:323,388` | `"linkedin.com"` in page.url → Easy Apply modal handling |
| `page_helpers.py:295` | `"linkedin.com/jobs/view"` |
| `lib/platforms/__init__.py:21` | `PLATFORMS = {"linkedin.com": "linkedin", ...}` (only 2!) |
| `lib/contacts/discover.py:94` | `"linkedin.com" in url` |
| `linkedin.py` (whole entrypoint) | LinkedIn scraper |
| `lib/linkedin_messaging.py` | LinkedIn DM composer |

### 3b. The target abstraction

**A single `platforms.json` data file** (like categories.json) driving the
registry:

```json
{
  "linkedin": {
    "domains": ["linkedin.com"],
    "session": true,
    "classify": "easy_apply",
    "skip_paths": ["/comm", "/feed", "/notifications", "/mynetwork", "/messaging"],
    "extractor": "linkedin",
    "messenger": "linkedin_messaging",
    "notes": "Easy Apply modal, DM composer, profile session"
  },
  "greenhouse": {
    "domains": ["greenhouse.io", "boards.greenhouse.io"],
    "session": true,
    "classify": "ats_direct",
    "extractor": "generic",
    "notes": "aria-owns comboboxes"
  }
}
```

And a registry loader (`lib/platforms/registry.py`) that:
- **URL → platform** (`platform_for(url)`) — used by detect, url_safety, creds,
  extract, check/fill/submit (replaces the scattered `"linkedin.com" in url`).
- **session allowlist** — built from `session: true` platforms (replaces
  `url_safety._SESSION_HOST_SUFFIXES`).
- **credential domains** — from `domains` (replaces `credentials.py` lists).
- **skip paths** — from `skip_paths` (replaces `extract._SKIP_DOMAINS`).
- **behavior hooks** — `extractor`/`messenger`/`classify` map to module names.

### 3c. Behavior modules (the extension point)

Each platform gets a module in `lib/platforms/` exposing an optional interface;
the registry dispatches. New ATS = add a JSON entry + optional module. No core
file changes:

```
lib/platforms/__init__.py      # registry dispatch (platform_for, is_session)
lib/platforms/registry.py      # loads platforms.json, caches
lib/platforms/_shared.py       # universal clean/fallback
lib/platforms/linkedin.py      # extractor + Easy Apply hooks + messaging
lib/platforms/greenhouse.py    # extractor + combobox notes
lib/platforms/generic.py       # fallback
```

`detect.py`, `check.py`, `fill.py`, `submit.py` replace their
`"linkedin.com" in page.url` checks with `platform_for(url).name == "linkedin"`.

---

## What this separation buys

1. **New user** — replace `profile.json` + `default_answers.json` + pronoun
   data. Zero code.
2. **New ATS ecosystem** — add `platforms.json` entries + optional modules.
   Zero core edits.
3. **Safer framework** — safety invariants (verify, guards, SPC) stay code and
   can't be "configured away" by a data edit.
4. **Future augmentation** — the platform registry is the natural seam for
   new widget handlers, extractors, and messengers.

---

## Action list (priority order)

1. **Pronoun derivation → data** (resolve.py:290-340). Move `gmap` +
   `"Yes"` into `pronouns.json` (or profile answers). (HARDCODED → data)
2. **`platforms.json` + registry** — consolidate the domain scatter from
   url_safety/credentials/extract/detect/check/fill/submit into one data file +
   `lib/platforms/registry.py`. (PLATFORM → abstraction)
3. **Replace `"linkedin.com" in page.url` checks** with `platform_for(url)`.
4. **Move `COUNTRY_ISO` + semantic ALIASES** to `data/` (reference data
   augmentation). (BORDERLINE → data)
5. **Consent auto-answer value → data** (fill_runner:512 `"true"` from a
   consent preference, not a literal).
6. **Document the layer contract** in this file as the canonical rule.
