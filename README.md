# Job Intelligence Skill

Pipeline for automated job discovery, description fetching, and CV tailoring. Replaces the compromised `gog` CLI with a Python Gmail API client.

## Components

### `gmail-cli/` — Gmail API CLI
Replacement for gog. Uses Google's official Python client.
```
gmail-cli auth credentials client_secret.json
gmail-cli auth add you@gmail.com
gmail-cli gmail search "newer_than:7d" --all -j
gmail-cli gmail get <threadId>
```

### `gemini-browser/` — Gemini Browser Automation
Connects to Chrome via CDP (puppeteer-core), navigates to a custom gem, sends prompts, reads responses. Uses Gemini 3.5 Flash + Extended thinking. Gem selection via `--gem` flag resolved through `gems.json`.
```
node gemini.js "your prompt"
node gemini.js --gem <alias_or_id> "prompt"
node gemini.js --gems
```

### `job_intelligence/` — Pipeline

The pipeline is SLM-orchestrated — each stage outputs options, the SLM admits/rejects/advances.

| Step | Script | SLM does |
|------|--------|----------|
| 1 | `stage_emails.py` | — (auto, filters by `job`/`jobs` keyword) |
| 2 | `extract.py` | `admit --category <name> <jid>` / `reject` / `reset <jid>` |
| 3 | `linkedin.py` | Same admit/reject as extract |
| 4 | `fetch.py` | `admit` / `reject` / `flag` (auth wall) / `help` |
| 5 | `tailor.py` | `done` / `skip` / `redo` / `retry` / `help` |
| — | `apply.py` | `auto <jid>` / `batch` |
| — | `report.py` | Read-only inspection: `stats`, `inspect`, `search`, `export`, `summary` |

Key subcommands across the pipeline: `help` (all stage scripts), `status` (extract, fetch, tailor).

### Categories

Each job has a category (`tech`, `general`) that determines which Gemini gem handles its tailoring. Set on first `admit` via `--category tech <jid>`. Resolved through `categories.json` → `gems.json` → `gemini.js`.

### Notes

Human context (referral mentions, priorities) attached via `submit`. `tailor.py` appends `Context: {notes}` to the Gemini prompt. Survives all stage transitions.

### Auth walls

Jobs behind sign-in walls are auto-detected during fetch. Flagged manually via `fetch.py flag <jid>`. Opened in Chrome's persistent session via `fetch.py open [<jid>]`. Stale entries auto-pruned.

### Per-job reset

Reset a specific job to re-extract: `extract.py reset <jid>`. Full pipeline wipe: `extract.py reset` (no args).

## Setup

1. **Chrome** must be running with remote debugging:
   ```
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="%USERPROFILE%\.openclaw\chrome-profile" --remote-debugging-port=9222 --no-first-run
   ```
2. **Gmail API credentials** from Google Cloud Console (enable Gmail API)
3. **Chrome persistent profile** at `~/.openclaw/chrome-profile/` (managed by `lib/chrome_manager.py`)

## Requirements

- Python 3.12+
- Node.js 20+
- Google Chrome
- Google Cloud project with Gmail API enabled
- Playwright (Python): `pip install playwright`
- puppeteer-core (Node): `npm install -g puppeteer-core`

## Output

Tailored CVs and application files go to `~/.openclaw/results/{jid}/`:
- `gemini_response.txt` — full Gemini output
- `script.py` — extracted Python script for PDF generation
- `{jid}.url` — shortcut to job posting
- `*.pdf` — generated CV / cover letter

## Recovery

| Signal | Fix |
|--------|------|
| `invalid_grant` | `python3 gmail-cli/gmail_cli.py auth add <email>` |
| `TIMEOUT` / `RATE_LIMIT` | `python3 tailor.py retry` |
| Chrome crash | Re-run the Chrome command from Setup step 1 |
| DB corruption | `python3 extract.py reset` |
| Auth wall stuck | `fetch.py open` + `fetch.py --refresh` |

## Pipeline in one line

```
stage_emails → extract → admit/reject → fetch → admit/reject → tailor → done/skip
```

Detailed pipeline reference: `job_intelligence/SKILL.md`
