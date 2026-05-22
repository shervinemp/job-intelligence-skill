"""pipeline.py — Orchestrator. Two commands: status, step."""

import json, os, sys, subprocess

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
NEEDS_AUTH_PATH = os.path.join(SKILL_DIR, "needs_auth.json")


def _run(*args, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("cwd", SKILL_DIR)
    return subprocess.run([sys.executable, *args], **kw)


def cmd_status():
    from lib.db import load_state, STAGES
    state = load_state()
    s = state.get("stages", {})
    extracted = s.get("extracted", 0)
    described = s.get("described", 0)
    tailored = s.get("tailored", 0)
    failed = s.get("failed", 0)

    auth_domains = []
    if os.path.exists(NEEDS_AUTH_PATH):
        try:
            entries = json.load(open(NEEDS_AUTH_PATH))
            auth_domains = sorted(set(e.get("domain", "?") for e in entries))
        except Exception:
            pass

    tokens = [f"EXTRACT:{extracted}", f"FETCH:{described}",
              f"TAILOR:{tailored}", f"FAILED:{failed}"]
    if auth_domains:
        tokens.append(f"AUTH:{'+'.join(auth_domains)}")
    print(" ".join(tokens))


def cmd_step():
    from lib.db import load_state

    state = load_state()
    described = state.get("stages", {}).get("described", 0)
    if described > 0:
        r = _run("tailor.py", "run-all", "--no-open")
        out = (r.stdout or "") + (r.stderr or "")
        for line in out.split("\n"):
            line = line.strip()
            if line.startswith("COMPLETE "):
                _, jid = line.split(None, 1)
                jid = jid.split()[0] if jid else "?"
                print(f"STEP:tailor OK {jid}")
                return
            if line.startswith("FAILED "):
                _, rest = line.split(None, 1)
                jid = rest.split()[0] if rest else "?"
                print(f"STEP:tailor FAIL {jid}")
                return
        print(f"STEP:tailor ?")
        return

    print("ALL_DONE")


def main():
    if len(sys.argv) < 2:
        print("Usage: pipeline.py <status|step>")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "step":
        cmd_step()
    else:
        print(f"Unknown: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
