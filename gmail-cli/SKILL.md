---
name: gmail-cli
description: Gmail CLI for job pipeline (secure Python replacement for gog).
metadata: {"clawdbot":{"emoji":"🐍","requires":{"bins":["python"]}}}
---

# gmail-cli

`skills/gmail-cli/gmail_cli.py` — uses Google Gmail API directly. Supports the Gmail + Auth subset needed by the job intelligence pipeline.

## Setup (once)
- `python3 skills/gmail-cli/gmail_cli.py auth credentials /path/to/client_secret.json`
- `python3 skills/gmail-cli/gmail_cli.py auth add you@gmail.com`
- `python3 skills/gmail-cli/gmail_cli.py auth list`

## Commands
- `python3 skills/gmail-cli/gmail_cli.py gmail search '<query>' --all -j`
- `python3 skills/gmail-cli/gmail_cli.py gmail get <messageId>`
- `python3 skills/gmail-cli/gmail_cli.py auth credentials <path>`
- `python3 skills/gmail-cli/gmail_cli.py auth add <email>`
- `python3 skills/gmail-cli/gmail_cli.py auth list`
- `python3 skills/gmail-cli/gmail_cli.py auth remove <email>`

## Notes
- Tokens stored at `~/.config/gmail-cli/tokens/<email>.json`
