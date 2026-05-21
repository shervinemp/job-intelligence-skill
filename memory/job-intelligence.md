# Job Intelligence SOP

When asked to run job intelligence, follow `AGENTS.md` in this repo — that is the canonical pipeline SOP.

## Quick reference
1. **Fetch**: `gmail-cli gmail search '<query>' --all -j > search_results.json`
2. **Stage**: `stage_emails.py` 
3. **Extract**: `extract.py step` (LLM identifies job URLs from emails)
4. **Fetch descriptions**: `fetch.py run --count 30`
5. **Review**: Read `DESC:` lines → `fetch.py admit/reject/flag`
6. **Tailor**: `tailor.py ready <jid>` then `tailor.py done <jid>`
