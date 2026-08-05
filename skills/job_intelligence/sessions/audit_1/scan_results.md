## Scan Results

### Roles discovered: 2

| Role | Entry points | Notes |
|------|-------------|-------|
| **LLM** | `tailor.py --jid`, `tailor.py build`, `tailor.py admit`, `tailor.py review`, `tailor.py reject`, `tailor.py retry`, `apply.py detect`, `apply.py act --fill`, `apply.py act --next`, `apply.py act --submit`, `apply.py verify`, `apply.py inspect`, `tailor.py undo` | All commands emit structured output (TYPE:, NEXT:, STATUS:). No auth required. |
| **Developer** | `profile.json`, `decisions.md`, `categories.json`, `.env`, `chrome-config.json`, `git push`, `pip install` | Configuration files. No CLI auth. |

### Quick findings
- **No command validation**: CLI accepts any JID; if invalid, error is printed but pipeline continues. No pre-command schema validation.
- **No rollback for submit**: `apply.py undo` moves back one stage but submit is irreversible (applied → can't undo).
- **No timeout on LLM handoff**: Pipeline emits NEXT and waits indefinitely. No timeout if LLM doesn't respond.
- **No state validation**: `state.json` can be corrupted or stale between commands. No checksum or version.
- **No multi-session safety**: Two detect commands on different jobs can overwrite state.json.

### Journeys per role

**LLM (5 journeys):**
1. Tailor a CV (write resume.json → build → review → admit)
2. Apply via standard ATS (detect → fill → next → submit → verify)
3. Apply via LinkedIn Easy Apply (detect → fill with flow hook)
4. Provide --answers mid-apply (pipeline emits unfilled → LLM responds)
5. Fix a bad resume (review → retry --feedback → rebuild)

**Developer (2 journeys):**
1. Set up configuration (profile.json, decisions.md, .env)
2. Batch process jobs (extract → enrich → tailor --auto)
