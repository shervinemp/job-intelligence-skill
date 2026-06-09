"""Centralised path configuration. All pipeline paths derive from JI_HOME.

Override via JI_HOME env var for CI, Docker, or custom setups.
Default: ~/.ji/
"""

import os
from pathlib import Path

JI_HOME = os.environ.get("JI_HOME", os.path.join(os.path.expanduser("~"), ".ji"))

STATE_DIR = os.path.join(JI_HOME, "state")
RESULTS_DIR = os.path.join(JI_HOME, "results")
SNAPSHOTS_DIR = Path(JI_HOME) / "snapshots"
CHROME_PROFILE = os.path.join(JI_HOME, "chrome-profile")

DB_PATH = os.path.join(STATE_DIR, "jobs.db")
STATE_PATH = os.path.join(STATE_DIR, "apply_state.json")
REGISTRY_PATH = os.path.join(STATE_DIR, "page_registry.json")
AUTH_WALLS_PATH = os.path.join(STATE_DIR, "needs_auth.json")
CHROME_CONFIG = os.path.join(JI_HOME, "chrome-config.json")
