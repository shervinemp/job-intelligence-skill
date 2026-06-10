# Gemini Browser

Controls Gemini custom gems via real Chrome session (no API key). Playwright connects to running Chrome CDP → navigates gem URL → sets 3.5 Flash + Extended thinking → sends prompt → reads response → deletes conversation.

## Files
| File | Purpose |
|------|---------|
| `gemini.js` | Main entry — activate gem, send prompt, read response |

## Usage
### Prerequisite
```
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="C:\Users\sherv\.ji\chrome-profile" --remote-debugging-port=9222 --no-first-run
```

### Commands
| Command | Action |
|---------|--------|
| `node gemini.js "prompt"` | Send prompt → App Optimizer gem → 3.5 Flash + Extended thinking |
| `node gemini.js --state` | Show current mode + thinking level |
| `node gemini.js --gems` | List sidebar gems |
| `node gemini.js --dump` | Dump full page structure (debug) |
| `node gemini.js --login` | Verify + save session |
| `node gemini.js --gem-url URL "prompt"` | Use custom gem URL |
| `node gemini.js --output FILE "prompt"` | Save response to file |
| `node gemini.js --app-dir DIR "prompt"` | Save response to DIR/gemini_response.txt |
| `node gemini.js --prompt-file FILE` | Read prompt from file (avoids CMD length limit) |

## How It Works
1. `connectOverCDP` → attaches to running Chrome via websocket
2. Tries port 9222 first (your main Chrome), falls back to launching own
3. Navigates `https://gemini.google.com/gem/{id}` (id passed via `--gem` CLI arg, resolved from `gems.json`)
4. Opens mode picker → selects 3.5 Flash → expands thinking level → selects Extended
5. Types prompt into `[contenteditable="true"]` → clicks send button
6. Polls for generation complete (waits for copy button to appear + stop button gone)
7. Extracts response via `message-content .markdown` or fallback body text
8. Cross-checks conversation title → deletes the chat (won't delete if conversation changed)

## Notes
- Chrome profile at `~/.ji/chrome-profile/` — persists, won't get wiped
- Session cookies last ~6 months  
- Chrome must run with `--remote-debugging-port=9222`
- Gem ID passed via `--gem <id>` CLI arg (resolved from `gems.json` by `call_gemini.py`)
- Cross-check before delete: captures conversation title after response, verifies match
