"""Subprocess management for gemini.js — prompt file, timeout handling."""

import json
import os
import re
import subprocess
import sys
import tempfile
import time

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_LIB_DIR)
_WORKSPACE_ROOT = os.path.abspath(os.path.join(_SKILL_DIR, "..", ".."))
GEMINI_JS = os.path.join(_WORKSPACE_ROOT, "skills", "gemini-browser", "gemini.js")


def _load_gem_id():
    env_path = os.path.join(_SKILL_DIR, ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^GEM_ID\s*=\s*(.+)$", line.strip())
                if m:
                    return m.group(1).strip()
    except Exception:
        pass
    return "4203d06f5d81"


GEM_ID = _load_gem_id()


def call_gemini_node(*args, timeout_seconds=600, **kwargs):
    """Run gemini.js. Prompt goes via file (avoids Windows cmd length limit).
    Returns (success, output_or_error)."""
    output_file = os.path.join(
        tempfile.gettempdir(), f"gemini_response_{int(time.time())}.txt"
    )
    kwargs["output"] = output_file

    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        arg_list = list(args[0])
    else:
        arg_list = list(args)
    cmd = ["node", GEMINI_JS] + arg_list
    cmd += [s for k, v in kwargs.items() for s in (f"--{k}", v)]
    gemini_dir = os.path.dirname(GEMINI_JS)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds, cwd=gemini_dir
        )
        if result.returncode != 0:
            if output_file and os.path.exists(output_file):
                with open(output_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if len(content) > 50:
                    return True, content
            return False, result.stderr.strip() or f"Exit code {result.returncode}"
        return True, result.stdout
    except subprocess.TimeoutExpired:
        if output_file and os.path.exists(output_file):
            with open(output_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if len(content) > 50:
                return True, content
        return False, "TIMEOUT"


def list_gems():
    success, output = call_gemini_node("--gems", timeout_seconds=20)
    if success:
        print(output, file=sys.stderr)
    else:
        print(f"Failed to list gems: {output}", file=sys.stderr)
    return success
