#!/usr/bin/env python3
"""04_resume.py — Handle the resume upload step in Easy Apply modal.
Selects "Upload resume" option and uploads the tailored PDF.
Must be run BEFORE 05_click_next.py on the resume step.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from lib.chrome_manager import connect

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")
with open(STATE_PATH) as f:
    state = json.load(f)

jid = state["jid"]
results_dir = os.path.join(os.path.expanduser("~"), ".openclaw", "results", jid)
resume_path = None
if os.path.isdir(results_dir):
    for f_name in os.listdir(results_dir):
        if "Resume" in f_name and f_name.endswith(".pdf"):
            resume_path = os.path.join(results_dir, f_name)
            break

if not resume_path:
    print("ERROR: no tailored resume PDF found", file=sys.stderr)
    sys.exit(1)

b, ctx = connect()
page = None
for p in ctx.pages:
    if '/jobs/view/' in p.url:
        page = p
        break
if not page:
    print("ERROR: no LinkedIn page found", file=sys.stderr)
    sys.exit(1)

# Check if we're on the resume step
dlg_text = page.evaluate("() => document.querySelector('[role=\"dialog\"]')?.innerText || ''")
if 'Upload resume' not in dlg_text and 'Select resume' not in dlg_text:
    print("Not on resume step — skipping", file=sys.stderr)
    print("NEXT: apply/linkedin/easy_apply/05_click_next.py", file=sys.stderr)
    sys.exit(0)

# Upload via file input directly (don't click "Upload resume" — that closes the modal)
file_input = page.query_selector('[role="dialog"] input[type="file"]')
if file_input:
    try:
        file_input.set_input_files(resume_path)
        print(f"Uploaded: {os.path.basename(resume_path)}", file=sys.stderr)
    except Exception as e:
        print(f"Upload failed: {e}", file=sys.stderr)
else:
    print("No file input found", file=sys.stderr)

print("NEXT: apply/linkedin/easy_apply/05_click_next.py", file=sys.stderr)
