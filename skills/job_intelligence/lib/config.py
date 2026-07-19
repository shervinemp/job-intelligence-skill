"""Centralised path configuration. All pipeline paths derive from JI_HOME.

Override via JI_HOME env var for CI, Docker, or custom setups.
Default: ~/.ji/

Auto-loads job_intelligence/.env (if exists) into os.environ so all
pipeline vars (JI_HOME, JI_TAILOR, GMAIL_SEARCH_QUERY) can come from
a single .env file. Existing env vars take precedence.
"""

import os
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent
_ENV_CANDIDATES = [
    _SKILL_ROOT / ".env",
    _SKILL_ROOT / "job_intelligence" / ".env",
]
for _ENV_PATH in _ENV_CANDIDATES:
    if _ENV_PATH.exists():
        with open(_ENV_PATH, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _key, _val = _line.split("=", 1)
                _key = _key.strip()
                _val = _val.strip().strip('"').strip("'")
                if _key and _val:
                    os.environ.setdefault(_key, _val)
        break

JI_HOME = os.environ.get("JI_HOME", os.path.join(os.path.expanduser("~"), ".ji"))

STATE_DIR = os.path.join(JI_HOME, "state")
RESULTS_DIR = os.path.join(JI_HOME, "results")
SNAPSHOTS_DIR = Path(JI_HOME) / "snapshots"
CHROME_PROFILE = os.path.join(JI_HOME, "chrome-profile")

DB_PATH = os.path.join(STATE_DIR, "jobs.db")
STATE_PATH = os.path.join(STATE_DIR, "apply_state.json")
# The user's answer profile lives with the skill, not JI_HOME (it is the input
# users edit; JI_HOME holds derived state).
PROFILE_PATH = str(Path(__file__).resolve().parent.parent / "profile.json")
REGISTRY_PATH = os.path.join(STATE_DIR, "page_registry.json")
AUTH_WALLS_PATH = os.path.join(STATE_DIR, "needs_auth.json")
CHROME_CONFIG = os.path.join(JI_HOME, "chrome-config.json")
