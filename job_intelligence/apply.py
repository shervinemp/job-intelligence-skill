"""apply.py — Run atomic apply scripts step by step.
Usage:
  python3 apply.py detect <jid>    # Classify job type
  python3 apply.py click <jid>     # Click/verify Easy Apply modal
  python3 apply.py read <jid>      # Read current modal state
  python3 apply.py fill <jid>      # Heuristic fill from profile
  python3 apply.py screen <jid>    # Handle screening questions
  python3 apply.py next <jid>      # Click Next/Review
  python3 apply.py submit <jid>    # Click Submit, verify
"""
import os, subprocess, sys, time

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
APPLY_DIR = os.path.join(SKILL_DIR, "apply")

SCRIPTS = {
    "detect": "linkedin/detect.py",
    "detect_ats": "detect_ats.py",
    "click": "linkedin/easy_apply/01_click.py",
    "read": "linkedin/easy_apply/02_read_state.py",
    "fill": "linkedin/easy_apply/03_fill_fields.py",
    "screen": "linkedin/easy_apply/04_screening.py",
    "resume": "linkedin/easy_apply/04_resume.py",
    "next": "linkedin/easy_apply/05_click_next.py",
    "submit": "linkedin/easy_apply/06_submit.py",
    "navigate": "linkedin/external/01_navigate.py",
    "detect_platform": "linkedin/external/02_detect_platform.py",
    "fill_external": "common/01_fill_fields.py",
    "next_external": "common/02_click_next.py",
    "submit_external": "linkedin/external/03_submit.py",
}

def run_script(name, jid):
    rel_path = SCRIPTS.get(name)
    if not rel_path:
        print(f"Unknown step: {name}", file=sys.stderr)
        return
    abs_path = os.path.join(APPLY_DIR, rel_path)
    if not os.path.exists(abs_path):
        print(f"Script not found: {abs_path}", file=sys.stderr)
        return
    r = subprocess.run(
        [sys.executable, abs_path, jid],
        cwd=SKILL_DIR,
    )
    return r.returncode

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 apply.py <step> <jid>", file=sys.stderr)
        print(f"Steps: {', '.join(SCRIPTS.keys())}", file=sys.stderr)
        sys.exit(1)
    step, jid = sys.argv[1], sys.argv[2]
    run_script(step, jid)

if __name__ == "__main__":
    main()
