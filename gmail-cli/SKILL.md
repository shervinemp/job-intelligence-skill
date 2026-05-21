---
name: gmail-cli
description: Gmail CLI for job pipeline (secure Python replacement for gog).
metadata: {}
---

# gmail-cli

Replaces gog with the Google Gmail API directly. Supports the Gmail + Auth subset needed by the job pipeline.

## Setup (once)
- `gmail-cli auth credentials /path/to/client_secret.json`
- `gmail-cli auth add you@gmail.com`
- `gmail-cli auth list`

## Commands
- `gmail-cli gmail search '<query>' --all -j`
- `gmail-cli gmail get <messageId>`
- `gmail-cli auth credentials <path>`
- `gmail-cli auth add <email>`
- `gmail-cli auth list`
- `gmail-cli auth remove <email>`

## Notes
- Tokens stored at `~/.config/gmail-cli/tokens/<email>.json`
