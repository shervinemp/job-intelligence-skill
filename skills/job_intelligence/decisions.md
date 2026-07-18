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

Exception: legal blocks (e.g., Quebec on-site). Not preference filters — they decide.

General rules:
1. **Legal/clearance blocks** → Skip. Don't spend time on CANNOT-hire locations.
2. **Preference filters** (relo, commute, hours, remote vs hybrid) → Answer the way that passes the filter. Let the company decide if it matters.
3. **Skill/experience checks** → Infer from resume and `profile.json`. When range is ambiguous (e.g. "3-5 years"), round up generously. Never volunteer gaps or lack. Generalize from adjacent work if the core skill transfers.
4. **Specific past events** (previously employed, criminal history) → Truthful unless trivial (e.g. expired parking ticket).

### Answer sourcing

Each answer is resolved from (in priority order):
1. `--answers` JSON (explicit overrides from the operator)
2. `common_answers` (reused values saved per key)
3. `profile.json` (canonical source of truth)
4. Resume inference (extrapolated from CV content)

The `profile.json` is where all personal data lives (years per skill group, work authorization, location preferences, availability). Do not hardcode profile values in this file — they change over time and per person. Update `profile.json` instead.

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
