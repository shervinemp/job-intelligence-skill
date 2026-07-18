# Decisions

## Pipeline admission (extract/enrich)

Judge by profession and legal fit. Reject true non-fits. Don't reject for preferences (remote vs hybrid, salary range, imposter syndrome) — let the company decide.

| If | Then |
|----|------|
| Wrong profession (hardware, embedded, non-tech roles) | Reject |
| Legal block (Quebec on-site/hybrid, requires clearance you lack) | Reject |
| Everything else (any tech role, any location that's not blocked) | Admit — you're a generalist |

## Application screening (apply.py)

**Don't self-reject.** Screening questions are meant to cull you. Answer to pass through, let company reject post-resume.

Exception: legal blocks (e.g., Quebec on-site, security clearance). Not preference filters, not skill doubt — they decide.

Answer sourcing hierarchy:
1. `--answers` JSON (explicit operator overrides)
2. `common_answers` (reused values per key, e.g. EEO)
3. `profile.json` (canonical data: years per skill group, authorization, availability, location)
4. Resume inference (extrapolate from CV content)

**Investigate before skipping.** Optional unfilled fields should be deduced from available context (job source, resume, prior answers, vision) before giving up. Do not guess from word-similarity alone.

### Relocation strategy

| Q | Strategy | Why |
|---|----------|-----|
| Relocate Canada-wide? | Yes | Broad market — say yes |
| Relocate US? | Yes + visa | Willing to sponsor, let them decide |
| Quebec / Montreal on-site/hybrid | No | Legal block — skip |
| Quebec / Montreal remote | Yes | Not physically there, no legal issue |
| Remote vs hybrid vs on-site | Prefer remote, accept hybrid, flexible on Ontario on-site | Preference — answer per job to pass |

### Sponsorship (profile-sourced)

| Q | Source | Strategy |
|---|--------|----------|
| Authorized to work in Canada? | profile | Truthful |
| Need Canada sponsorship? | profile | Truthful |
| Need US sponsorship? | profile | Truthful |

### Experience (profile-sourced)

Don't lie. Estimate generously from total years in profile.

| Q | Source | Strategy |
|---|--------|----------|
| Years in core skill (e.g. Python, backend, web) | profile | Core skillset, round up |
| Years in adjacent tech (AWS, Docker, CI/CD) | profile | Touched these, round up |
| Specific framework unfamiliar? | — | Don't volunteer gaps. Answer "No" unless asked directly |
| "Done X specific task?" | resume | Yes if adjacent. Generalize from similar work |
| Previously employed by [Company]? | profile | Truthful |

### Logistics (profile-sourced)

| Q | Source | Strategy |
|---|--------|----------|
| Available start date? | profile | Truthful |
| OT / weekends? | — | Yes — market conditions |
| Criminal record? | profile | Truthful |

### Location rules

| Location | Decision |
|----------|----------|
| Ontario (Toronto, Ottawa, Oakville, Mississauga, Waterloo...) | Yes — preferred |
| Other Canada (Vancouver, Calgary... on-site/hybrid) | Yes |
| Remote / work-from-home (any country) | Yes |
| Quebec / Montreal on-site/hybrid | No |
| Quebec / Montreal remote | Yes — not physically in Quebec |
| US on-site only | No |
| Unclear | Fetch description first, then decide |
