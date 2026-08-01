# AGENTS.md

## Core Principles
- **Sequential:** One session/agent. No parallelism.
- **Red Lines:** No private data exfiltration. No destructive commands (trash > rm). Ask when in doubt.
- **Safe:** read files, search web/calendar, workspace. **Ask First:** emails/tweets/posts, anything leaving machine.
- **No assumptions:** use profile/common_answers data before deciding. If data doesn't cover it, ask.

## Session & Memory
- Startup: runtime context (AGENTS.md, SOUL.md, USER.md). Don't reread.
- No mental notes. Write to files. Text > Brain.
- Compression: `GUIDELINES.md` for high-density rewriting.

## Job Pipeline
- **Flow:** `stage_emails.py` → `extract.py` → `enrich.py` → `tailor.py`. See `SKILL.md` for full commands.
- **LinkedIn:** `linkedin.py [--max N]` as alt entry point.
- **Reach (outreach):** after `enrich.py admit --team <name>` → `reach.py discover <jid>` (or `reach.py discover --all`). Then `reach.py list <jid>` → `reach.py email --dry-run` → send, or `reach.py message` / `reach.py connect`. Gmail send needs `gmail-cli auth add <email> --services gmail.send` once.
- **Recovery:** auth → `gmail-cli auth add` | Chrome crash → `Start-Process ... --remote-debugging-port=9222` | FAILED → `retry` | SKIPPED → `retry-skipped`
- **Output:** `~/.ji/results/{jid}/`
- **Submit safety:** Submit is one-shot. `submit_clicked` flag prevents re-clicking. If outcome uncertain, pipeline investigates (success signals, URL change, form gone, validation errors, vision API) — never clicks twice. `--force` for manual retry only. `undo` clears the flag.
- **Outreach safety:** same one-shot philosophy — `email_sent`/`message_sent` flags, `--force` re-send, `reach.py undo <jid>` resets. Unconfirmed DMs report `uncertain` (never silently resend; verify in inbox, then `reach.py update --set-sent`).
- **When stuck:** Investigate (`act --inspect`) before retrying. Don't guess — read the page, check the HTML dump, analyze the screenshot. Only ask the human when all automated detection methods are exhausted.

## Tools & Automation
- **Skills:** `SKILL.md`. Setup: `README.md` (Quick Start + Requirements).
- **Proactive (2-4x daily):** emails, calendar, mentions, weather.
- **Reach out:** urgent email, calendar <2h, interesting info, >8h since last check.
- **Silent (HEARTBEAT_OK):** late night (23:00-08:00), busy, nothing new.
