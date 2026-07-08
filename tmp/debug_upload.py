import sys, json
sys.path.insert(0, r'C:\Users\sherv\.openclaw\workspace\skills\job_intelligence')
from lib.chrome_manager import connect
from apply.common.page_helpers import load_state

state = load_state()
b, ctx = connect()
for p in ctx.pages:
    if 'linkedin.com/jobs' in p.url:
        dlg = p.evaluate("() => !!document.querySelector('[role=dialog], dialog')")
        print(f'Dialog open: {dlg}', file=sys.stderr)
        if dlg:
            spans = p.evaluate("""() => {
                const d = document.querySelector('[role="dialog"]') || document.querySelector('dialog');
                if (!d) return [];
                return Array.from(d.querySelectorAll('span')).map(s => s.textContent.trim());
            }""")
            print(f'Spans: {spans[:10]}', file=sys.stderr)
        break
b.close()
