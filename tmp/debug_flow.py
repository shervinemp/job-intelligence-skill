import sys
sys.path.insert(0, r'C:\Users\sherv\.openclaw\workspace\skills\job_intelligence')
from lib.chrome_manager import connect
from apply.common.page_helpers import load_state
from apply.common.registry import resolve
from apply.common.handler_base import run_modal_flow
import time

state = load_state()
jid = '21d9556b4a472e9a'
b, ctx = connect()

page = None
for p in ctx.pages:
    if 'linkedin.com/jobs' in p.url:
        page = p
        break
if not page:
    page = ctx.new_page()
    page.goto(state.get('external_url', ''), wait_until='domcontentloaded', timeout=30000)
    time.sleep(3)

ext = state.get('external_url', '')
reg = resolve(ext or page.url)
handler = reg.get_handler() if reg else None

print(f'Handler: {type(handler).__name__ if handler else None}', file=sys.stderr)

# Step-by-step
import json
modal = handler.ensure_modal_open(page)
print(f'ensure_modal_open: {modal}', file=sys.stderr)

st = handler.detect(page)
print(f'detect: has_dialog={st.has_dialog} resume_step={st.resume_step} is_applied={st.is_applied} buttons={st.buttons}', file=sys.stderr)
for f in st.fields:
    print(f'  field: key={f.key} value={f.value!r} required={f.required}', file=sys.stderr)

if st.resume_step:
    res = handler.ensure_resume(page, jid)
    print(f'ensure_resume: {res}', file=sys.stderr)

# Fill fields
from apply.common.resolve import resolution_for_fill
for f in st.fields:
    if f.required and not f.value:
        res = resolution_for_fill(f.key, {})
        print(f'  resolve: {f.key} -> {res.value if res else None}', file=sys.stderr)

b.close()
