# AGENTS.md

## Core Principles
- **Sequential:** One session/agent. No parallelism.
- **Red Lines:** No private data exfiltration. No destructive commands (trash > rm). Ask when in doubt.
- **Safe:** read files, search web/calendar, workspace. **Ask First:** emails/tweets/posts, anything leaving machine.

## Session & Memory
- Startup: runtime context (AGENTS.md, SOUL.md, USER.md). Don't reread.
- No mental notes. Write to files. Text > Brain.
- Compression: `GUIDELINES.md` for high-density rewriting.

## Workflow & Data Management
- **Stage-and-Delegate:** fetch/clean → DB stage → **LLM extraction** → score/rank → enrichment → Notion.
  - LLM-first: all inference via LLM. Regex fallback secondary.
  - Never delete scripts/tools. Only `state/` DB, `results/` cleared at request.
  - Clean Pass: `extract.py reset` wipes everything.
- **Sub-agents (Enrichment Only):** `{json_entry} + {file_path}` → enriched JSON → Notion + `manager.py submit`.
- **Data Cleaning:** Strip HTML via `clean_html()` in `stage_emails.py`.

## Communication
- **Groups:** Participant, not proxy. Human's stuff → don't share.
- **Speak:** mentioned, adding value, correcting, summarizing. **Silent (HEARTBEAT_OK):** casual, someone else answered, flowing.
- **Reactions:** 👍 ❤️ 😂 🤔 ✅ 👀. Max 1/message.
- **Format:** Discord/WhatsApp: no tables (lists), wrap links `<>`. WhatsApp: no headers (bold/CAPS).

## Job Pipeline
- **Flow:** `stage_emails.py` → `extract.py` → `fetch.py` → `tailor.py`. See `SKILL.md` for full commands.
- **LinkedIn:** `linkedin.py [--max N]` as alt entry point.
- **Recovery:** auth → `gmail-cli auth add` | Chrome crash → `Start-Process ... --remote-debugging-port=9222` | FAILED → `retry` | SKIPPED → `retry-skipped`
- **Output:** `~/.openclaw/results/{jid}/`

## Opencode (Standalone CLI)
- PTY crashes TUI on tool calls. `--attach` mode eats stdout → useless.
- **Task:** `opencode run "<task>" --agent build -m opencode-go/deepseek-v4-flash --dir "C:\Users\sherv\Desktop\projects\HeistMasters" -c`
- **Multi-round:** same `-c` continues last session. Run again with updated task.
- **Sessions:** `opencode session list`. Auto-creates new if none exists.
- No subagent relay. No PTY. No serve. Just CLI.
- **Plan→Build:** `--agent plan` first, `--agent build` next (same session).
- **Never dump full conversation history into opencode.** Just the task.

### Opencode Execution Patterns (learned 2026-05-17)
- `--format json` → stdout = context bomb (logs + thinking tokens + full file contents). Never live-capture.
- **Mandatory:** `2>&1 | Tee-Object -FilePath "$env:TEMP\opencode.jsonl"` — pipe to file.
- **`--fork`** required per task (prevents context bloat → crashes).
- Delegate full workflow internally (polish → commit → push). Don't split steps → saves double-work.
- **Never poll:** `process poll/log` dumps JSONL into our context too. Every poll = wasted context.
- **Real fix = subagent:** `sessions_spawn(task="<brief task>")` — subagent runs opencode + poll + result extraction, we get one clean summary.
- Memory loop warning: writing same file 8+ times → context exhaustion. One write, move on.

## Tools & Automation
- **Skills:** `SKILL.md`. Setup: `TOOLS.md`.
- **Voice:** `sag` (ElevenLabs TTS).
- **Heartbeat:** batching, context, drift OK.
- **Cron:** exact timing, isolation, different model/thinking.
- **Proactive (2-4x daily):** emails, calendar, mentions, weather.
- **Reach out:** urgent email, calendar <2h, interesting info, >8h since last check.
- **Silent (HEARTBEAT_OK):** late night (23:00-08:00), busy, nothing new.
