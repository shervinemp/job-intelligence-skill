# AGENTS.md

## Core Principles
- **Sequential:** One session/agent. No parallelism.
- **Red Lines:** No private data exfiltration. No destructive commands (trash > rm). Ask when in doubt.
- **Safe:** read files, search web/calendar, workspace. **Ask First:** emails/tweets/posts, anything leaving machine.

## Session & Memory
- Startup: runtime context (AGENTS.md, SOUL.md, USER.md). Don't reread.
- No mental notes. Write to files. Text > Brain.
- Compression: `GUIDELINES.md` for high-density rewriting.

## Job Pipeline
- **Flow:** `stage_emails.py` → `extract.py` → `fetch.py` → `tailor.py`. See `SKILL.md` for full commands.
- **LinkedIn:** `linkedin.py [--max N]` as alt entry point.
- **Recovery:** auth → `gmail-cli auth add` | Chrome crash → `Start-Process ... --remote-debugging-port=9222` | FAILED → `retry` | SKIPPED → `retry-skipped`
- **Output:** `~/.ji/results/{jid}/`

## Tools & Automation
- **Skills:** `SKILL.md`. Setup: `TOOLS.md`.
- **Proactive (2-4x daily):** emails, calendar, mentions, weather.
- **Reach out:** urgent email, calendar <2h, interesting info, >8h since last check.
- **Silent (HEARTBEAT_OK):** late night (23:00-08:00), busy, nothing new.
