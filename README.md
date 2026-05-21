# Job Intelligence Skill

Pipeline for automated job discovery, description fetching, and CV tailoring. Replaces the compromised `gog` CLI with a Python Gmail API client.

## Components

### `gmail-cli/` — Gmail API CLI
Replacement for gog. Same interface (`search`, `get`, `auth`), uses Google's official Python client.
```
gmail-cli auth credentials client_secret.json
gmail-cli auth add you@gmail.com
gmail-cli gmail search "newer_than:7d" --all -j
gmail-cli gmail get <threadId>
```

### `gemini-browser/` — Gemini Browser Automation
Connects to Chrome via CDP, navigates to a custom gem, sends prompts, reads responses. Uses 3.5 Flash + Extended thinking (no Pro dependency).
```
node gemini.js "your prompt"
node gemini.js --state
```

### `job_intelligence/` — Pipeline
| Step | Script | What it does |
|------|--------|-------------|
| 1 | `stage_emails.py` | Fetches email threads from search results via gmail-cli |
| 2 | `extract.py step` | LLM reads emails, identifies job URLs, fetches + parses them |
| 3 | `fetch.py run` | Fetches descriptions for admitted jobs |
| 4 | `tailor.py ready/done` | Generates tailored CV via Application Optimizer gem |

## Setup

1. **Chrome** must be running with remote debugging:
   ```
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="%USERPROFILE%\.openclaw\chrome-profile" --remote-debugging-port=9222 --no-first-run
   ```
2. **Gmail API credentials** from Google Cloud Console (enable Gmail API)
3. **Git credentials** stored in `.env` (see `.env.example`)

## Pipeline in one line

```
gmail-cli search → stage_emails → extract.py step → fetch.py run → DESC review → tailor.py ready/done
```

## Requirements

- Python 3.12+
- Node.js 20+ with `playwright-core` (`npm install -g playwright-core`)
- Google Chrome
- Google Cloud project with Gmail API enabled
