import sys
sys.path.insert(0, r'C:\Users\sherv\.openclaw\workspace\skills\job_intelligence')
from lib.chrome_manager import connect
b, ctx = connect()
for p in ctx.pages:
    if 'linkedin.com/jobs' in p.url:
        info = p.evaluate("""() => {
            const d = document.querySelector('[role="dialog"]') || document.querySelector('dialog');
            if (!d) return 'NO DIALOG';
            const btns = d.querySelectorAll('button');
            const results = [];
            for (const b of btns) {
                results.push({
                    tag: b.tagName,
                    text: (b.textContent || '').trim(),
                    visible: b.offsetParent !== null,
                    disabled: b.disabled,
                });
            }
            // Also check all visible elements with 'Next' text
            const all = d.querySelectorAll('*');
            for (const el of all) {
                if (el.offsetParent === null) continue;
                const t = (el.textContent || '').trim();
                if (t === 'Next') {
                    results.push({tag: el.tagName, text: t, note: 'found via wildcard'});
                }
            }
            return results;
        }""")
        import json
        for r in info:
            print(json.dumps(r))
        break
b.close()
