#!/usr/bin/env python3
"""03_fill_fields.py — Fill LinkedIn Easy Apply modal fields.
Thin wrapper around common/01_fill_fields.py for the LinkedIn modal context.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from lib.chrome_manager import connect

# Find the linkedin page
b, ctx = connect()
page = None
for p in ctx.pages:
    if '/jobs/view/' in p.url:
        page = p
        break
if not page:
    print("ERROR: no LinkedIn page found", file=sys.stderr)
    sys.exit(1)

# Mark and delegate to common filler
page.evaluate("() => window.__applyPage = true")

# Re-exec as the common filler with the matched jid
common_path = os.path.join(os.path.dirname(__file__), "..", "..", "common", "01_fill_fields.py")
os.execv(sys.executable, [sys.executable, common_path] + sys.argv[1:])
