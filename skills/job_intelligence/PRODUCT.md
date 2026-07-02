# Product

## Register
CLI pipeline tool (Python + Playwright)

## Users
| Role | Description |
|------|-------------|
| **LLM** | Primary operator. Reads pipeline output, makes decisions, provides `--answers`, writes `resume.json`, reviews quality, runs commands. |
| **Developer** | Configures profile.json, decisions.md, categories.json. Handles infrastructure, Chrome setup, environment variables. |

## Product Purpose
Automate job applications: discover jobs from email, extract details, enrich with descriptions, tailor CVs, and apply via browser automation.

## Routes by role

### LLM
| Route (command) | Entry | Exit |
|-----------------|-------|------|
| Tailor CV | `tailor.py --jid <jid>` → reads `prompt.txt` → writes `resume.json` | `tailor.py admit <jid>` |
| Apply (standard) | `apply.py detect <jid>` → `act --fill` → `act --next` → `act --submit` | `apply.py verify <jid>` |
| Apply (LinkedIn) | `apply.py detect <jid>` → `act --fill` (flow hook handles all) | Flow hook returns "done" |
| Provide answers | Pipeline emits unfilled fields → LLM reads field labels + options | `act --fill --answers '...'` |
| Review quality | `tailor.py review [--jobs N]` | `admit` / `reject` / `retry --feedback` |
| Handle errors | Pipeline emits error → LLM reads and decides | `retry`, `retry --feedback`, or manual fix |

### Developer
| Route (command) | Entry | Exit |
|-----------------|-------|------|
| Setup | Edit `profile.json`, `decisions.md`, `categories.json` | Pipeline reads config |
| Batch process | `tailor.py --auto`, `extract.py`, `enrich.py` | Jobs advance through stages |
| Infrastructure | Set `JI_TAILOR`, `LLM_API_URL`, Chrome config | Pipeline connects |
