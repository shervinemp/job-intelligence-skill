"""Subprocess management for gemini.js — prompt file, timeout handling."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_LIB_DIR)
_WORKSPACE_ROOT = os.path.abspath(os.path.join(_SKILL_DIR, "..", ".."))
GEMINI_JS = os.path.join(_WORKSPACE_ROOT, "skills", "gemini-browser", "gemini.js")
_GEMS_PATH = os.path.join(_SKILL_DIR, "gems.json")

_NODE_BIN = shutil.which("node")
if not _NODE_BIN:
    print("ERROR: Node.js not found in PATH. Install Node.js 20+ and try again.", file=sys.stderr)
    sys.exit(1)

# Auto-detect node_modules: check workspace root first, then up-tree
_NODE_MODULES = os.path.join(_WORKSPACE_ROOT, "node_modules")
if not os.path.isdir(_NODE_MODULES):
    _NODE_MODULES = os.path.join(os.path.dirname(_WORKSPACE_ROOT), "node_modules")
if os.path.isdir(_NODE_MODULES):
    os.environ.setdefault("NODE_PATH", _NODE_MODULES)


def call_gemini_node(*args, timeout_seconds=600, gem=None, **kwargs):
    """Run gemini.js. Prompt goes via file (avoids Windows cmd length limit).
    Args:
        gem: gem alias or raw ID (resolved via gems.json, None = use .env default)
    Returns (success, output_or_error)."""
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="gemini_resp_", suffix=".txt", delete=False,
        encoding="utf-8"
    ) as tf:
        output_file = tf.name

    kwargs["output"] = output_file

    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        arg_list = list(args[0])
    else:
        arg_list = list(args)
    cmd = [_NODE_BIN, GEMINI_JS] + arg_list
    if gem:
        # Resolve alias to raw ID via gems.json before passing to gemini.js
        try:
            with open(_GEMS_PATH) as f:
                gems = json.load(f)
            if gem in gems:
                gem = gems[gem]
        except Exception:
            pass
        cmd += ["--gem-id", gem]
    cmd += [s for k, v in kwargs.items() for s in (f"--{k}", v)]
    gemini_dir = os.path.dirname(GEMINI_JS)
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=gemini_dir
        )
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
        stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""

        if proc.returncode != 0:
            if os.path.exists(output_file):
                with open(output_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if len(content) > 50:
                    return True, content
            try:
                err = json.loads(stderr_str)
                if err.get("error") == "RATE_LIMIT":
                    reset = err.get("resetsAt", "unknown")
                    return False, f"RATE_LIMIT:{reset}"
            except (json.JSONDecodeError, AttributeError):
                pass
            return False, stderr_str.strip() or f"Exit code {proc.returncode}"
        return True, stdout_str.strip()
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        if os.path.exists(output_file):
            with open(output_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if len(content) > 50:
                return True, content
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)[:200]
    finally:
        try:
            if os.path.exists(output_file):
                os.remove(output_file)
        except Exception:
            pass


def list_gems():
    success, output = call_gemini_node("--gems", timeout_seconds=20)
    if success:
        print(output, file=sys.stderr)
    else:
        print(f"Failed to list gems: {output}", file=sys.stderr)
    return success
