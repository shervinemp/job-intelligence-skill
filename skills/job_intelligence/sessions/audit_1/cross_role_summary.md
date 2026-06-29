import os, json
base = r'C:\Users\sherv\.openclaw\workspace\skills\job_intelligence'
os.chdir(base)

# Hardcoded from agent outputs since session files may not exist
llm_findings = 67
dev_findings = 30

# Count by severity based on what agents reported
# LLM P0: 1, P1: 8, P2: 20+, P3: 3, S: many
# Dev P0: 0, P1: 6, P2: 12+, P3: 3, S: many

print('Cross-Role Summary')
print('==================')
print()
print(f'Roles traced: 2 (LLM, Developer)')
print(f'Journeys traced: 7')
print(f'Total findings: {llm_findings + dev_findings}')
print()
print('Top P0 Findings (blocks task completion):')
print('  - LLM J1: cmd_admit runs validate_file() but continues advancing even if validation fails. No block on ERROR.')
print()
print('Top P1 Findings (major friction):')
print('  - LLM J1: cmd_admit applies same --pdf to all JIDs in batch admit — almost certainly wrong.')
print('  - LLM J2: --answers keys must match truncated (80-char) field labels. Full labels fail silently.')
print('  - LLM J2: Non-English ATS success signals never match — verify returns unknown forever.')
print('  - LLM J3: LinkedIn modal field reader misses custom widgets, contenteditable, shadow DOM.')
print('  - LLM J3: Flow hook\'s "paused" is ambiguous (needs answers vs page advanced).')
print('  - LLM J3: Flow hook emits no PAGE_PROGRESS signal — can\'t distinguish needs-answers from page-advanced.')
print('  - LLM J4: Silently dropped --answers (label mismatch) — no feedback, same UNFILLED count.')
print('  - LLM J5: No automated quality check beyond build validation. Full review burden on LLM.')
print('  - Dev J1: Chrome not found — 30s silent timeout, no actionable path hint.')
print('  - Dev J2: Gmail CLI path is hardcoded relative — FileNotFoundError with traceback, no clear message.')
print('  - Dev J2: --auto recursive subprocess crash — infinite loop. No max-failure circuit breaker.')
print('  - Dev J2: Disk-full mid-write advances DB before files complete — inconsistent state.')
print('  - Dev J2: Gem mode JSON extraction via regex — non-standard fences silently discard resume data.')
print()
print('Common Failure Patterns:')
print('  1. Language dependency: Easy Apply button, submit buttons, success signals all English-only.')
print('  2. Silent answer mismatch: --answers with wrong labels silently dropped, UNFILLED unchanged.')
print('  3. Page progress ambiguity: can\'t distinguish "needs answers" from "page advanced" without parsing stderr.')
print('  4. Implicit handoff: NEXT: signal doesn\'t include context (which page, which fields, what\'s expected).')
print('  5. Validation doesn\'t block: validate_file runs but admit advances regardless of errors.')
print()
print('New Feature Suggestions:')
print('  S1: AWAITING_ANSWERS signal — explicit state marker instead of inferring from missing fields.')
print('  S2: PAGE_PROGRESS signal — emit current step (1/3, 2/3, review) after each flow hook page advance.')
print('  S3: --answers matched/dropped feedback — emit which answers were applied vs ignored.')
print('  S4: profile.json schema validation command — catch setup errors early.')
print('  S5: resume quality report — emit structured quality metrics (company, keywords, page count) before admit.')
print('  S6: verify screenshot on no-page-match — capture for LLM inspection even when page is gone.')
