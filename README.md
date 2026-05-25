# Job Intelligence Pipeline

> Automated job discovery, description fetching, and CV tailoring — orchestrated by SLM.

```
                         ┌──────────┐
                         │  Gmail   │
                         │  Search  │
                         └────┬─────┘
                              │
                         ┌────▼─────┐
                         │  stage_  │
                         │ emails   │
                         └────┬─────┘
                              │
                         ┌────▼─────┐     ┌──────────┐
                         │ extract  │────▶│ linkedin │
                         │   .py    │     │   .py    │
                         └────┬─────┘     └────┬─────┘
                              │                │
                         ┌────▼─────┐          │
                         │  admit / │◀─────────┘
                         │  reject  │
                         └────┬─────┘
                              │
                         ┌────▼─────┐
                         │  fetch   │
                         │   .py    │
                         └────┬─────┘
                              │
                         ┌────▼─────┐
                         │  admit / │
                         │reject/flg│
                         └────┬─────┘
                              │
                         ┌────▼─────┐
                         │  tailor  │
                         │   .py    │
                         └────┬─────┘
                              │
                         ┌────▼─────┐
                         │  done /  │
                         │   skip   │
                         └──────────┘
```

---

## Components

### <picture><source media="(prefers-color-scheme: dark)" srcset="https://api.iconify.design/logos:google-gmail.svg"><img src="https://api.iconify.design/logos:google-gmail.svg" width="20" height="20" align="top"></picture> `gmail-cli/` — Gmail API CLI

Python replacement for the compromised `gog` CLI. Uses Google's official Python client.

```
gmail-cli auth credentials client_secret.json
gmail-cli auth add you@gmail.com
gmail-cli gmail search "newer_than:7d" --all -j
gmail-cli gmail get <threadId>
```

### <picture><source media="(prefers-color-scheme: dark)" srcset="https://api.iconify.design/logos:gemini.svg"><img src="https://api.iconify.design/logos:gemini.svg" width="20" height="20" align="top"></picture> `gemini-browser/` — Gemini Automation

Connects to Chrome via CDP (puppeteer-core), navigates to a custom gem, sends prompts, reads responses. Uses Gemini 3.5 Flash + Extended thinking. No Pro dependency.

```
node gemini.js "your prompt"
node gemini.js --gem optimizer "Refactor this code"
node gemini.js --gems                           # list available gems
```

Gem aliases resolved through `gems.json`. Rate-limit detection built in.

### ⚙️ `job_intelligence/` — Pipeline Core

Each stage is a CLI script. The SLM reads the output and decides what to do next.

| Stage | Script | SLM Action |
|-------|--------|------------|
| 1. Stage emails | `stage_emails.py` | Auto (filters by `job`/`jobs` keyword) |
| 2. Extract URLs | `extract.py` | `admit --category <name> <jid>` or `reject` |
| 3. LinkedIn scrape | `linkedin.py` | Same admit/reject as extract |
| 4. Fetch descriptions | `fetch.py` | `admit`, `reject`, `flag` for auth walls |
| 5. Tailor CV | `tailor.py` | `done`, `skip`, `redo`, `retry` |
| — Auto-apply | `apply.py` | `auto <jid>`, `batch` |
| — DB reports | `report.py` | `stats`, `inspect`, `search`, `export`, `summary` |

All stage scripts support `help` and `status` subcommands.

---

## Key Features

### 🏷️ Categories

Jobs tagged with a category (`tech`, `general`) on first admit. Determines which Gemini gem handles tailoring. Resolved automatically: `categories.json` → `gems.json` → `gemini.js`.

```
python3 extract.py admit --category tech abc123def4567890
```

### 📝 Notes

Attach human context (referral mentions, priorities, etc.) to any job. Survives all stage transitions. Appended to the Gemini prompt as supplementary info.

```
python3 extract.py submit '{"url":"https://...","notes":"John can refer at Google"}'
```

### 🔒 Auth Walls

Jobs behind sign-in walls are auto-detected during fetch. Flag manually, open in Chrome's persistent session to bypass.

```
python3 fetch.py flag <jid>
python3 fetch.py open [<jid>]
```

### 🔄 Per-Job Reset

Reset a single job to re-extract it. Full wipe with no args.

```
python3 extract.py reset <jid>     # re-extract one job
python3 extract.py reset           # wipe everything, start fresh
```

---

## Quick Start

```bash
# 1. Start Chrome with persistent profile
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="$env:USERPROFILE\.openclaw\chrome-profile" --remote-debugging-port=9222 --no-first-run

# 2. Set up Gmail API
gmail-cli auth credentials client_secret.json
gmail-cli auth add you@gmail.com

# 3. Run the pipeline
python3 stage_emails.py
python3 extract.py              # review URLs → admit --category tech <jid>
python3 fetch.py                # review descriptions → admit <jid>
python3 tailor.py               # review CV → done <jid>
```

---

## Requirements

| Dependency | Version | Install |
|------------|---------|---------|
| Python | 3.12+ | — |
| Node.js | 20+ | — |
| Google Chrome | Latest | — |
| Playwright (Python) | — | `pip install playwright` |
| puppeteer-core (Node) | — | `npm install -g puppeteer-core` |
| Google Cloud project | — | Enable Gmail API |

---

## Output

Tailored files land in `~/.openclaw/results/{jid}/`:

```
📁 results/{jid}/
├── gemini_response.txt   # Full Gemini output
├── script.py             # Extracted Python script for PDF
├── {jid}.url             # Browser shortcut to job posting
└── *.pdf                 # Generated CV / cover letter
```

---

## Recovery

| Symptom | Fix |
|---------|-----|
| `invalid_grant` | Re-authenticate: `gmail-cli auth add <email>` |
| `TIMEOUT` / `RATE_LIMIT` | `python3 tailor.py retry` |
| Chrome crash | Restart Chrome from Quick Start step 1 |
| DB corruption | `python3 extract.py reset` |
| Auth wall stuck | `fetch.py open` then `fetch.py --refresh` |

---

> **Detailed operations manual**: `job_intelligence/SKILL.md`
