# Technical notes

Context pointer from `SKILL.md` — runtime internals, consulted on demand. Chrome lifecycle and the PDF guard live inline in SKILL.md (they matter at run time).

- **JI_TAILOR**: `"agent"` (default) = SLM writes `resume.json`, `admit` confirms. `"gem"` = Gemini Web gem.
- **Gemini.js**: `call_gemini.py` auto-detects `node_modules` (workspace root, parent chain).
- **LinkedIn title dedup**: Cards repeat title — duplicates are detected at add time via `find_duplicate` in `lib/db/jobs.py`.
- **Common_answers**: `--answers` exact → common_answers (exact optional, prefix required) → profile. Never pre-populate — save only user-provided values.
- **EEO detection**: Uses decline-option content ("prefer not to answer", "decline"), not label keywords — language-agnostic, zero false positives. Saved under `common_answers.eeo` sub-key.
- **Gems**: `categories.json` → `gems.json` → `gemini.js` resolution chain.
- **Type normalization at boundaries**: All external data sources (profile.json, --answers JSON, common_answers) are normalized to their expected types at the load point, not at each consumer. If a value can be int or string (`"salary": 120000`), it's normalized to string once. If `common_answers` is accidentally a string instead of dict, it's coerced to `{}` at validation time. Add new normalizations to `_validate_profile` in `act.py` or the `--answers` parse block — never guard at individual access sites.
- **Provenance tracking**: Every filled field is tagged with its source (`profile`, `answers`, `auto_decline`, `file`). The submit preview groups fields by provenance so all LLM-provided answers appear in one block for self-audit. Cross-page field values are tracked in `_field_values_history` and compared at submit time via `_reconcile_fields` — mismatches are printed before confirm.
- **Format hints**: Unfilled fields in dry-run and fill-report display expected format hints derived from HTML type/attributes and label keywords (phone → digits only, salary → numeric, date → MM/DD/YYYY). Also shows `max N chars` and `pattern=...` from HTML attributes. Hints are informational only — the LLM decides how to use them.
- **DIAG diagnostics**: Structured `DIAG:` lines emitted on fill failures — truncation (`DIAG: Phone | expected=+1 (343)... | actual=+1 (343) 5 | truncated | maxlength=10`), verify failure, and delta mismatch (unchanged / still empty / cleared). Machine-parseable for the LLM orchestrator to auto-correct on next iteration.
- **Vision fallback probe**: When all DOM probe strategies return 0 fields and `ask_api.available()` is True, the probe cascade takes a screenshot and asks the vision LLM to identify form fields. Best-effort — labels are fuzzy-matched to DOM elements by text proximity. Last resort before `html_scan`.
