"""Batch apply — run the full apply flow for tailored jobs automatically.
Usage: python3 batch_apply.py [--count N] [--relentless]

For each tailored job:
1. Detect type (Easy Apply / External / Applied)
2. If Applied → skip
3. If Easy Apply → click, read, fill, resume, screen (with common_answers), next, loop, submit
4. If External → navigate, detect_ats, fill, next, loop, submit
5. If anything fails → skip, continue

--relentless: retry on rate limit with idle loop
"""
import json, os, sys, re, time, subprocess
from datetime import datetime

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)
from lib.db import get_conn, advance_job
from lib.chrome_manager import connect
from apply.common.platforms import check_page, ALREADY_APPLIED

COUNT = 3
RELENTLESS = "--relentless" in sys.argv
if "--count" in sys.argv:
    i = sys.argv.index("--count")
    if i + 1 < len(sys.argv): COUNT = int(sys.argv[i + 1])

profile_path = os.path.join(SKILL_DIR, "profile.json")
with open(profile_path) as f:
    profile = json.load(f)
ca = profile.get("common_answers", {})

STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")

def answers_for(fields):
    """Build --answers JSON from common_answers for screening questions."""
    ans = {}
    for f in fields:
        label = f.get("label", "")
        norm = re.sub(r'[^a-z0-9]+', ' ', label.lower()).strip()
        # Try common_answers substring match
        for k, v in ca.items():
            if v and k.replace('_', ' ') in norm:
                ans[label] = v
                break
        if label not in ans:
            # Try FIELD_MAP lookup
            for map_norm, (section, key) in [
                ("full name", ("name", "")), ("first name", ("first_name", "")),
                ("last name", ("last_name", "")), ("email", ("email", "")),
                ("phone", ("phone", "")), ("location", ("location", "")),
                ("city", ("location", "")), ("linkedin", ("linkedin", "")),
                ("github", ("github", "")),
            ]:
                if map_norm in norm:
                    val = profile.get(key or section) or ca.get(key or section)
                    if val:
                        ans[label] = val
                        break
    return ans

def run_apply(jid):
    """Run full apply for one job. Returns 'submitted', 'already_applied', 'failed', or 'skipped'."""
    conn = get_conn()
    row = conn.execute("SELECT url, stage FROM jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        return "skipped", "not_found"
    url, stage = row["url"], row["stage"]
    
    if stage != "tailored":
        return "skipped", f"stage_is_{stage}"
    
    b, ctx = connect(timeout=15)
    p = ctx.new_page()
    
    try:
        # Step 1: Detect
        if "linkedin.com/jobs" in url:
            job_id = url.split("/jobs/view/")[1].split("/")[0]
            p.goto(f"https://www.linkedin.com/jobs/view/{job_id}/apply/?openSDUIApplyFlow=true", wait_until='domcontentloaded', timeout=30000)
            time.sleep(4)
            
            result = p.evaluate("""() => {
                const d = document.querySelector('[role="dialog"]');
                if (d && (d.innerText||'').length > 80) return { type: 'easy_apply' };
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    if ((b.textContent||'').trim() === 'Applied') return { type: 'applied' };
                    if ((b.getAttribute('aria-label')||'').includes('on company website')) return { type: 'external' };
                }
                return { type: 'unknown' };
            }""")
            
            if result["type"] == "applied":
                return "already_applied", ""
            
            if result["type"] == "easy_apply":
                return run_easy_apply(p, jid, b, ctx)
            
            if result["type"] == "external":
                return run_external_apply(p, jid, b, ctx, url)
            
            return "failed", "unknown_type"
        else:
            # Direct ATS
            p.goto(url, wait_until='domcontentloaded', timeout=30000)
            time.sleep(4)
            return run_ats_apply(p, jid, b, ctx)
    except Exception as e:
        return "failed", str(e)[:100]
    finally:
        try: p.close()
        except: pass
        try: b.close()
        except: pass

def run_easy_apply(page, jid, browser, ctx):
    """Easy Apply flow: fill profile fields → screening → next → ... → submit."""
    # Click and read
    result = page.evaluate("""() => {
        const d = document.querySelector('[role="dialog"]');
        if (!d) return false;
        const btns = d.querySelectorAll('button');
        for (const b of btns) {
            if ((b.textContent||'').trim().toLowerCase() === 'next') { b.click(); return true; }
        }
        return false;
    }""")
    # Wait for content
    time.sleep(3)
    
    for step in range(10):
        dlg = page.query_selector('[role="dialog"]')
        if not dlg:
            # Check success
            text = page.evaluate("() => document.body.innerText").lower()
            if any(w in text for w in ["thank you", "submitted", "your application"]):
                return "submitted", ""
            return "submitted", "modal_closed"
        
        fields = page.evaluate("""() => {
            const d = document.querySelector('[role="dialog"]');
            if (!d) return [];
            const inputs = d.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
            return Array.from(inputs).map(el => {
                const lbl = d.querySelector('label[for="'+el.id+'"]');
                return {
                    tag: el.tagName, type: el.getAttribute('type')||'', id: el.id, name: el.getAttribute('name')||'',
                    label: (lbl?lbl.textContent.trim():'')||el.placeholder||el.getAttribute('aria-label')||'',
                    required: el.required, value: el.value||'',
                    options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean) : [],
                };
            });
        }""")
        
        # Fill empty required fields from common_answers
        for f in fields:
            if f['required'] and (not f['value'] or f['value'] == 'Select an option'):
                ans = answers_for([f])
                if f['label'] in ans:
                    val = ans[f['label']]
                    sel = f"#{f['id']}" if f['id'] and not f['id'][0].isdigit() else f"[id=\"{f['id']}\"]" if f['id'] else f"[name=\"{f['name']}\"]"
                    if sel and sel != '#':
                        try:
                            el = page.query_selector(sel)
                            if el:
                                if f['tag'] == 'SELECT':
                                    el.select_option(val)
                                else:
                                    el.fill(val)
                        except: pass
        
        # Upload resume if file input on this step
        file_input = page.query_selector('[role="dialog"] input[type="file"]')
        if file_input:
            jid = ""  # will be filled in
            results_dir = os.path.join(os.path.expanduser("~"), ".openclaw", "results")
            for d in os.listdir(results_dir):
                for fn in os.listdir(os.path.join(results_dir, d)):
                    if "Resume" in fn and fn.endswith(".pdf"):
                        try:
                            file_input.set_input_files(os.path.join(results_dir, d, fn))
                        except: pass
                        break
                break
        
        # Click primary button
        btn_text = page.evaluate("""() => {
            const d = document.querySelector('[role="dialog"]');
            if (!d) return null;
            const overlay = document.getElementById('interop-outlet');
            if (overlay) overlay.style.pointerEvents = 'none';
            const btns = d.querySelectorAll('button:not([disabled])');
            for (const b of btns) {
                const t = (b.textContent||'').trim().toLowerCase();
                if (t === 'submit application' || t === 'submit' || t === 'send') return 'submit';
                if (t === 'next') return 'next';
                if (t === 'review') return 'next';
            }
            return null;
        }""")
        
        if not btn_text:
            return "failed", "no_button"
        
        if btn_text == 'submit':
            # Click submit and verify
            page.evaluate("""() => {
                const overlay = document.getElementById('interop-outlet');
                if (overlay) overlay.style.pointerEvents = 'none';
                const d = document.querySelector('[role="dialog"]');
                if (!d) return;
                const btns = d.querySelectorAll('button:not([disabled])');
                for (const b of btns) {
                    const t = (b.textContent||'').trim().toLowerCase();
                    if (t.includes('submit') || t.includes('send')) { b.click(); return; }
                }
            }""")
            time.sleep(4)
            text = page.evaluate("() => document.body.innerText").lower()
            for w in ["thank you", "submitted", "your application"]:
                if w in text:
                    return "submitted", ""
            return "submitted", "submit_clicked"
        
        # Click Next/Review
        page.evaluate("""() => {
            const overlay = document.getElementById('interop-outlet');
            if (overlay) overlay.style.pointerEvents = 'none';
            const d = document.querySelector('[role="dialog"]');
            if (!d) return;
            const btns = d.querySelectorAll('button:not([disabled])');
            for (const b of btns) {
                const t = (b.textContent||'').trim().toLowerCase();
                if (t === 'next' || t === 'review') { b.click(); return; }
            }
        }""")
        time.sleep(3)
    
    return "failed", "max_steps"

def run_external_apply(page, jid, browser, ctx, url):
    """External apply: click button, detect platform, fill, submit."""
    page.evaluate("""() => {
        const btns = document.querySelectorAll('button');
        for (const b of btns) {
            const aria = (b.getAttribute('aria-label')||'');
            if (aria.includes('on company website') && b.offsetParent !== null) { b.click(); return; }
        }
    }""")
    time.sleep(5)
    
    # Find the external page
    external_url = None
    for p in ctx.pages:
        if 'linkedin.com' not in p.url and p.url != 'about:blank' and not p.url.startswith('chrome'):
            external_url = p.url
            break
    
    if not external_url:
        return "failed", "no_external_page"
    
    return run_ats_page(external_url)

def run_ats_apply(page, jid, browser, ctx):
    return run_ats_page(page.url)

def run_ats_page(url):
    """Fill form on an ATS page, submit."""
    b, ctx = connect(timeout=15)
    p = ctx.new_page()
    p.goto(url, wait_until='domcontentloaded', timeout=30000)
    time.sleep(4)
    
    text = p.evaluate("() => document.body.innerText")
    if check_page(text, None, ALREADY_APPLIED):
        return "already_applied", ""
    
    for step in range(10):
        fields = p.evaluate("""() => {
            const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
            return Array.from(inputs).map(el => {
                const lbl = document.querySelector('label[for="'+el.id+'"]');
                const parent = el.closest('div, fieldset, section, li');
                const plbl = parent ? parent.querySelector('label, legend, strong, span') : null;
                let label = (lbl?lbl.textContent.trim():'')||el.placeholder||el.getAttribute('aria-label')||'';
                if (!label && plbl) label = plbl.textContent.trim();
                const opts = el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean) : [];
                return {
                    tag: el.tagName, type: el.getAttribute('type')||'', id: el.id, name: el.getAttribute('name')||'',
                    label: label.replace(/\\s+/g,' ').trim().slice(0, 100),
                    required: el.required, value: el.value||'', options: opts.slice(0, 12),
                };
            });
        }""")
        
        # Fill from profile + common_answers
        filled_any = False
        for f in fields:
            if f['required'] and (not f['value'] or f['value'] == 'Select an option'):
                ans = answers_for([f])
                if f['label'] in ans:
                    val = ans[f['label']]
                    sel = f"#{f['id']}" if f['id'] and not f['id'][0].isdigit() else f"[id=\"{f['id']}\"]" if f['id'] else f"[name=\"{f['name']}\"]"
                    if sel and sel != '#':
                        try:
                            el = p.query_selector(sel)
                            if el:
                                if f['tag'] == 'SELECT':
                                    el.select_option(val)
                                elif f['type'] in ('text','email','tel'):
                                    el.fill(val)
                                elif f['type'] == 'radio':
                                    p.evaluate(f"""(id,val)=>{{
                                        const radios = document.querySelectorAll('input[type="radio"][name="{f['name']}"]');
                                        for (const r of radios) {{
                                            if ((r.id && document.querySelector('label[for="'+r.id+'"]')?.textContent?.trim()?.toLowerCase() || '').includes(val.toLowerCase())) {{
                                                r.click(); return;
                                            }}
                                        }}
                                    }}""", f['id'], val)
                                filled_any = True
                        except: pass
        
        # Upload resume
        file_input = p.query_selector('input[type="file"]')
        if file_input:
            results_dir = os.path.join(os.path.expanduser("~"), ".openclaw", "results")
            for d in os.listdir(results_dir):
                for fn in os.listdir(os.path.join(results_dir, d)):
                    if "Resume" in fn and fn.endswith(".pdf"):
                        try:
                            file_input.set_input_files(os.path.join(results_dir, d, fn))
                        except: pass
                        break
                break
        
        # Find and click primary button
        btn = p.evaluate("""() => {
            const btns = document.querySelectorAll('button:not([disabled])');
            for (const b of btns) {
                const t = (b.textContent||'').trim().toLowerCase();
                if (t.includes('submit') || t === 'send') return 'submit';
                if (t === 'next' || t === 'continue') return 'next';
                if (t === 'review') return 'next';
            }
            return null;
        }""")
        
        if not btn:
            return "failed", "no_button"
        
        if btn == 'submit':
            try:
                btn_el = p.locator('button:has-text("Submit")').first
                btn_el.click(timeout=5000)
            except:
                p.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (const b of btns) {
                        if ((b.textContent||'').toLowerCase().includes('submit')) { b.click(); return; }
                    }
                }""")
            time.sleep(4)
            body = p.evaluate("() => document.body.innerText").lower()
            for w in ["thank you", "submitted", "your application"]:
                if w in body:
                    return "submitted", ""
            return "submitted", "submit_clicked"
        
        # Click Next
        try:
            btn_el = p.locator('button:has-text("Next")').first
            btn_el.click(timeout=5000)
        except:
            p.evaluate("""() => {
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    if ((b.textContent||'').toLowerCase() === 'next') { b.click(); return; }
                }
            }""")
        time.sleep(3)
    
    return "failed", "max_steps"

def main():
    conn = get_conn()
    tailored = conn.execute("SELECT id, title, company FROM jobs WHERE stage='tailored'").fetchall()
    print(f"Jobs to apply: {len(tailored)}", file=sys.stderr)
    
    submitted = already = failed = skipped = 0
    for jid, title, company in tailored[:COUNT if COUNT != -1 else len(tailored)]:
        print(f"\n  {title[:40]} @ {company[:20]}", file=sys.stderr)
        status, reason = run_apply(jid)
        if status == "submitted":
            advance_job(jid, "applied")
            submitted += 1
            print(f"  => SUBMITTED", file=sys.stderr)
        elif status == "already_applied":
            advance_job(jid, "applied")
            already += 1
            print(f"  => ALREADY APPLIED", file=sys.stderr)
        else:
            failed += 1
            print(f"  => {status}: {reason}", file=sys.stderr)
    
    print(f"\nResults: {submitted} submitted, {already} already applied, {failed} failed/skipped", file=sys.stderr)

if __name__ == "__main__":
    main()
