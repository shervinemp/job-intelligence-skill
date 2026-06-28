"""Subprocess management for gemini.js — output-file protocol.
gemini.js writes structured JSON to the output file on ALL exit paths.
call_gemini.py reads the output file as the single source of truth.
stdout/stderr are diagnostics only (not parsed for data)."""
import json, os, re, shutil, subprocess, sys, tempfile, time

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_LIB_DIR)
_WORKSPACE_ROOT = os.path.abspath(os.path.join(_SKILL_DIR, "..", ".."))
GEMINI_JS = os.path.join(_WORKSPACE_ROOT, "skills", "gemini-browser", "gemini.js")
_GEMS_PATH = os.path.join(_SKILL_DIR, "gems.json")

_NODE_BIN = shutil.which("node")
if not _NODE_BIN:
    print("ERROR: Node.js not found in PATH. Install Node.js 20+ and try again.", file=sys.stderr)
    sys.exit(1)

_NODE_MODULES = os.path.join(_WORKSPACE_ROOT, "node_modules")
if not os.path.isdir(_NODE_MODULES):
    _NODE_MODULES = os.path.join(os.path.dirname(_WORKSPACE_ROOT), "node_modules")
if os.path.isdir(_NODE_MODULES):
    os.environ.setdefault("NODE_PATH", _NODE_MODULES)


def _parse_wait(resets_at: str) -> int:
    """Parse human-readable reset time, return seconds to wait (min 30)."""
    now = __import__('datetime').datetime.now()
    m = re.search(r'(\d+)\s*(minute|hour)', resets_at, re.I)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return max(30, n * (60 if unit.startswith('m') else 3600))
    for fmt in ("%b %d, %I:%M %p", "%b %d %I:%M %p", "%I:%M %p", "%H:%M"):
        try:
            dt = __import__('datetime').datetime.strptime(resets_at.strip(), fmt)
            if fmt in ("%I:%M %p", "%H:%M"):
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
                if dt < now:
                    dt += __import__('datetime').timedelta(days=1)
            return max(30, int((dt - now).total_seconds()))
        except ValueError:
            continue
    return 3600


def call_gemini_node(*args, timeout_seconds=600, gem=None, **kwargs):
    """Run gemini.js. Structured output goes to a temp file (single source of truth).
    Auto-retries on rate limit. Returns (success, output_or_error)."""
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="gemini_resp_", suffix=".json", delete=False, encoding="utf-8"
    ) as tf:
        output_file = tf.name

    kwargs["output"] = output_file

    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        arg_list = list(args[0])
    else:
        arg_list = list(args)
    cmd = [_NODE_BIN, GEMINI_JS] + arg_list
    if gem:
        try:
            with open(_GEMS_PATH) as f:
                gems = json.load(f)
            if gem in gems:
                gem = gems[gem]
        except Exception:
            pass
        cmd += ["--gem", gem]
    cmd += [s for k, v in kwargs.items() for s in (f"--{k}", v)]
    gemini_dir = os.path.dirname(GEMINI_JS)

    def read_output():
        if os.path.exists(output_file):
            try:
                with open(output_file, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return None

    max_retries = 3
    for attempt in range(max_retries):
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=gemini_dir
            )
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
            # Forward gemini.js stderr diagnostics (includes deleteChat debug logs)
            if stderr:
                for line in stderr.decode("utf-8", errors="replace").split("\n"):
                    line = line.strip()
                    if line:
                        print(f"  [gemini] {line}", file=sys.stderr)
            # Read output file first (single source of truth)
            data = read_output()
            if data:
                status = data.get("status")
                if status == "ok":
                    return True, data.get("response", "")
                if status == "rate_limit":
                    resets_at = data.get("resetsAt", "unknown")
                    if attempt + 1 < max_retries:
                        wait = _parse_wait(resets_at) + 30
                        print(f"[call_gemini] Rate limited until {resets_at}, waiting {wait}s...", file=sys.stderr)
                        time.sleep(wait)
                        continue
                    return False, f"RATE_LIMIT:{resets_at}"
                if status == "error":
                    return False, data.get("message", "Unknown error")
            # No output file — fall back to streams
            if proc.returncode != 0:
                return False, (stderr or b"").decode("utf-8", errors="replace").strip() or f"Exit code {proc.returncode}"
            return True, (stdout or b"").decode("utf-8", errors="replace").strip()
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            data = read_output()
            if data and data.get("status") == "ok":
                return True, data["response"]
            if attempt + 1 < max_retries:
                print("[call_gemini] Timeout, retrying...", file=sys.stderr)
                continue
            return False, "TIMEOUT"
        except Exception as e:
            if attempt + 1 < max_retries:
                continue
            return False, str(e)[:200]
        finally:
            try:
                if os.path.exists(output_file):
                    os.remove(output_file)
            except Exception:
                pass
    return False, "Max retries exceeded"


def list_gems():
    success, output = call_gemini_node("--gems", timeout_seconds=20)
    if success:
        print(output, file=sys.stderr)
    else:
        print(f"Failed to list gems: {output}", file=sys.stderr)
    return success
