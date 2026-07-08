"""Batch apply for LinkedIn Easy Apply jobs.
Runs detect + multi-step apply for each tailored job.
"""
import sys, os, time, json, subprocess

BASE = r'C:\Users\sherv\.openclaw\workspace\skills\job_intelligence'
sys.path.insert(0, BASE)
os.chdir(BASE)

from lib.db import get_conn

# Get all tailored LinkedIn jobs (excluding the already-applied one)
conn = get_conn()
rows = conn.execute(
    'SELECT id, title, company FROM jobs WHERE stage = "tailored" ORDER BY RANDOM()'
).fetchall()

print(f"Found {len(rows)} tailored jobs", file=sys.stderr)

for jid, title, company in rows:
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Job: {title} @ {company} ({jid})", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    # Detect
    r = subprocess.run(
        [sys.executable, 'apply.py', 'detect', jid],
        capture_output=True, text=True, timeout=120,
    )
    output = r.stderr + r.stdout
    print(output, file=sys.stderr)
    
    if 'TYPE: easy_apply' not in output:
        print(f"SKIP: not easy_apply", file=sys.stderr)
        continue
    
    # Apply (allow submit via live mode)
    env = os.environ.copy()
    env['JI_APPLY_MODE'] = 'live'
    r = subprocess.run(
        [sys.executable, 'apply.py', 'act', '--fill', jid],
        capture_output=True, text=True, timeout=180, env=env,
    )
    output = r.stderr + r.stdout
    print(output, file=sys.stderr)
    
    if 'submitted' in output.lower() or 'applied' in output.lower():
        print(f"✓ APPLIED: {title} @ {company}", file=sys.stderr)
    elif 'flow_failed' in output:
        print(f"✗ FAILED: flow hook could not proceed", file=sys.stderr)

print(f"\nDone.", file=sys.stderr)
