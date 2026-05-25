"""apply.py — Auto-apply via subprocess. Usage: python apply.py auto <jid>"""

import json, os, subprocess, sys, time

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))

from lib.db import load, advance


def _load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


AUTO_APPLY_JS = os.path.join(SKILL_DIR, "lib", "auto_apply.py")


def auto_apply(jid):
    try:
        r = subprocess.run(
            [sys.executable, AUTO_APPLY_JS, "--jid", jid],
            capture_output=True, text=True, timeout=180, cwd=SKILL_DIR,
        )
        out = (r.stdout or "").strip()
        if out:
            return json.loads(out)
        err = (r.stderr or "").strip()[:200]
        return {"status": "error", "jid": jid, "reason": f"no_output:{err}"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "jid": jid, "reason": "timeout"}
    except json.JSONDecodeError:
        return {"status": "error", "jid": jid, "reason": "bad_json"}


def cmd_auto(jid):
    result = auto_apply(jid)
    status = result.get("status", "failed")
    reason = result.get("reason", "")
    print(json.dumps(result))
    if status in ("submitted", "already_applied"):
        state = load()
        if jid in state.get("jobs", {}):
            advance(state["jobs"][jid], "applied",
                    applied_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    print(f"  {status.upper()}:{jid}:{reason}", file=sys.stderr)


def cmd_batch(count=1):
    state = load()
    tailored = [(jid, e) for jid, e in state["jobs"].items()
                if e.get("stage") == "tailored"]
    if not tailored:
        print("NO_TAILORED", file=sys.stderr)
        return
    applied = bailed = 0
    for jid, entry in tailored[:count]:
        print(f"  {entry.get('title')} @ {entry.get('company')}", file=sys.stderr)
        r = auto_apply(jid)
        status = r.get("status", "failed")
        reason = r.get("reason", "")
        print(json.dumps(r))
        if status in ("submitted", "already_applied"):
            state = load()
            if jid in state.get("jobs", {}):
                advance(state["jobs"][jid], "applied",
                        applied_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
                applied += 1
        else:
            bailed += 1
        time.sleep(1)
    print(f"APPLIED:{applied} BAILED:{bailed}", file=sys.stderr)


def cmd_help():
    print("Usage:", file=sys.stderr)
    print("  auto <jid>          Auto-apply for a specific job", file=sys.stderr)
    print("  batch [--count N]   Batch apply (default 1)", file=sys.stderr)
    print("  help                This message", file=sys.stderr)


def main():
    if len(sys.argv) < 3 and not (len(sys.argv) == 2 and sys.argv[1] == "help"):
        print("Usage: python apply.py auto <jid> | batch [--count N] | help", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "auto":
        cmd_auto(sys.argv[2])
    elif cmd == "batch":
        count = 1
        if "--count" in sys.argv:
            i = sys.argv.index("--count")
            if i + 1 < len(sys.argv):
                count = int(sys.argv[i + 1])
        cmd_batch(count)
    elif cmd == "help":
        cmd_help()
    else:
        print(f"Unknown: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
