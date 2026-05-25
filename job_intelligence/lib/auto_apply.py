"""Standalone apply subprocess. Usage: python lib/auto_apply.py --jid <jid>
Returns JSON line on stdout: {"status":"submitted"|"failed"|"blocked", ...}
"""
import json, os, sys, time, re, hashlib
sys.stdout.reconfigure(encoding="utf-8")

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL_DIR)

from lib.chrome_manager import connect
from lib.db import get_conn, app_get
from lib.call_gemini import call_gemini_node

RESULTS = os.path.join(os.path.expanduser("~"), ".openclaw", "results")

def die(status, jid, reason, **kw):
    out = {"status": status, "jid": jid, "reason": reason, **kw}
    print(json.dumps(out))
    sys.exit(0)

# ─── Profile ────────────────────────────────────────

def load_profile():
    path = os.path.join(SKILL_DIR, "profile.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)

# ─── Field label → profile mapper ───────────────────

FIELD_MAP = {
    "first name": "first_name", "firstname": "first_name", "first_name": "first_name",
    "last name": "last_name", "lastname": "last_name", "last_name": "last_name",
    "email": "email", "email address": "email", "e-mail": "email",
    "phone": "phone", "phone number": "phone", "mobile": "phone", "telephone": "phone",
    "city": "location", "location": "location",
    "linkedin": "linkedin", "linkedin profile": "linkedin", "linkedin url": "linkedin",
    "github": "github", "github url": "github",
    "portfolio": "portfolio", "website": "website",
    "resume": "resume", "upload resume": "resume", "cv": "resume",
    "cover letter": "cover_letter",
}

COMMON_ANSWERS = {
    "work authorization": "work_authorization", "authorized to work": "authorized_to_work",
    "visa": "require_visa", "visa sponsorship": "visa_sponsorship",
    "sponsorship": "visa_sponsorship", "require visa": "require_visa",
    "gender": "gender", "race": "gender", "ethnicity": "gender",
    "veteran": "veteran_status", "disabled": "disability_status", "disability": "disability_status",
    "how did you hear": "how_did_you_hear", "referral": "how_did_you_hear",
    "relocate": "willing_to_relocate", "relocation": "willing_to_relocate",
    "current salary": "current_ctc", "expected salary": "expected_ctc",
    "notice period": "notice_period",
}

def resolve_field(label, profile):
    norm = re.sub(r'[^a-z0-9]+', ' ', label.lower()).strip()
    # Try direct match
    key = FIELD_MAP.get(norm) or FIELD_MAP.get(label.lower().strip())
    if key:
        if key == "resume":
            return None  # handled separately
        val = profile.get(key) or profile.get("common_answers", {}).get(key)
        if val:
            return val
    # Try common_answers multi-word match
    ca = profile.get("common_answers", {})
    for phrase, ca_key in COMMON_ANSWERS.items():
        if phrase in norm:
            val = ca.get(ca_key)
            if val:
                return val
    return None

# ─── Platform detection ─────────────────────────────

def detect_platform(url):
    if "linkedin.com/jobs" in url:
        return "linkedin"
    host = url.split("/")[2] if "//" in url else ""
    for kw, plat in [
        ("greenhouse", "greenhouse"), ("lever.co", "lever"),
        ("myworkdayjobs", "workday"), ("workday.com", "workday"),
        ("icims.com", "icims"), ("taleo.net", "taleo"),
        ("smartrecruiters", "smartrecruiters"), ("bamboohr", "bamboohr"),
        ("ashbyhq.com", "ashby"), ("jazzhr.com", "jazzhr"),
    ]:
        if kw in host or kw in url:
            return plat
    return None

# ─── LinkedIn Easy Apply handler ─────────────────────

def handle_linkedin(page, jid, url, profile):
    m = re.search(r'/jobs/view/(\d+)', url)
    if m:
        job_id = m.group(1)
    
    page.goto(f"https://www.linkedin.com/jobs/search/?f_AL=true&keywords=software&location=Canada", wait_until='domcontentloaded', timeout=30000)
    time.sleep(3)
    
    # Click the specific job card
    cards = page.query_selector_all(f'.job-card-container[data-job-id="{job_id}"]')
    if cards:
        cards[0].click()
    else:
        # Fallback: click any card then navigate
        card = page.query_selector('.job-card-container')
        if card: card.click()
    time.sleep(3)
    
    # Check for "Applied" button (means already applied)
    applied_btn = page.evaluate("""() => {
        const pane = document.querySelector('.jobs-search__job-details--container');
        if (!pane) return false;
        const btns = pane.querySelectorAll('button');
        for (const b of btns) {
            const t = (b.textContent || '').trim().toLowerCase();
            if (t === 'applied') return true;
            if (t === 'applied \\u2713') return true;
        }
        return false;
    }""")
    if applied_btn:
        return {"status": "already_applied", "jid": jid}
    
    # Check for Easy Apply / Apply buttons (including external links)
    result = page.evaluate("""() => {
        const pane = document.querySelector('.jobs-search__job-details--container');
        if (!pane) return { action: 'no_pane' };
        const all = pane.querySelectorAll('button, a');
        for (const el of all) {
            const t = (el.textContent || '').trim().toLowerCase();
            if (t === 'easy apply' && !el.disabled) { el.click(); return { action: 'easy_apply' }; }
            if (t === 'apply' && !el.disabled) {
                const href = el.getAttribute('href') || el.href || '';
                return { action: 'external', url: href };
            }
            if ((t === "i'm interested" || t === 'i\u2019m interested') && !el.disabled) { el.click(); return { action: 'interested' }; }
        }
        return { action: 'no_button' };
    }""")
    
    if result['action'] == 'external':
        target_url = result.get('url', '')
        if target_url and not target_url.startswith('http'):
            # Relative URL — prepend LinkedIn domain
            target_url = 'https://www.linkedin.com' + target_url
        if not target_url:
            die("failed", jid, "external apply with no URL")
        page.goto(target_url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(3)
        # Detect platform and fill
        plat = detect_platform(target_url)
        if not plat:
            die("failed", jid, f"unknown ATS platform")
        result_val = fill_modal(page, jid, profile)
        if isinstance(result_val, dict):
            return result_val
        return result_val
    
    if result['action'] == 'no_pane' or result['action'] == 'no_button':
        die("failed", jid, f"no apply button found ({result['action']})")
    
    if result['action'] != 'easy_apply':
        # "I'm interested" was clicked — check if Easy Apply modal appears after
        time.sleep(3)
        d = page.evaluate("() => document.querySelector('[role=\"dialog\"]') ? true : false")
        if not d:
            die("failed", jid, f"clicked '{clicked}' but no modal appeared")
    
    time.sleep(2)
    return fill_modal(page, jid, profile)

# ─── Generic modal/ATS field filler ──────────────────

def fill_modal(page, jid, profile):
    """Multi-step modal filler. Detects fields, fills, clicks Next/Submit."""
    last_hash = None
    max_steps = 10
    last_action = None
    
    for step in range(max_steps):
        time.sleep(2)
        
        dlg = page.query_selector('[role="dialog"]')
        if not dlg:
            if step == 0:
                return {"status": "failed", "jid": jid, "reason": "no_dialog"}
            # Dialog disappeared — check if we submitted
            text = page.evaluate("() => document.body.innerText").lower()
            for w in ["thank you", "application submitted", "your application", "was sent"]:
                if w in text:
                    return {"status": "submitted", "jid": jid}
            if last_action == "submit":
                return {"status": "submitted", "jid": jid}
            return {"status": "failed", "jid": jid, "reason": "dialog_closed"}
        
        # Check for success text inside dialog
        dlg_text = dlg.inner_text().lower()
        for w in ["application sent", "submitted", "thank you"]:
            if w in dlg_text:
                return {"status": "submitted", "jid": jid}
        
        # Extract form fields (don't trust prefilled values)
        fields = page.evaluate("""(container) => {
            const dlg = document.querySelector('[role="dialog"]');
            if (!dlg) return [];
            const inputs = dlg.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea');
            return Array.from(inputs).map(el => {
                const lbl = dlg.querySelector('label[for="' + el.id + '"]');
                const parent = el.closest('div, fieldset, section');
                const parentLabel = parent ? parent.querySelector('label, .label, legend, [role=heading]') : null;
                const labelText = lbl ? lbl.textContent.trim() : (parentLabel ? parentLabel.textContent.trim() : '');
                const opts = el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean) : [];
                return {
                    tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '', id: el.id,
                    name: el.getAttribute('name') || '',
                    label: labelText || el.getAttribute('placeholder') || el.getAttribute('aria-label') || '',
                    required: el.required, value: el.value || '', options: opts,
                };
            });
        }""")
        
        if not fields:
            # No inputs — click primary button
            action = click_primary(page)
            if not action:
                return {"status": "failed", "jid": jid, "reason": "no_button"}
            last_action = action
            continue
        
        # Fill fields from profile
        fill_fields(page, fields, profile, jid)
        
        # If any required fields remain empty, use LLM to resolve
        unfilled = [f for f in fields if f["required"] and not f["value"]]
        if unfilled:
            llm_ok = llm_fill_fields(page, unfilled, fields, profile)
            if not llm_ok:
                return {"status": "failed", "jid": jid, "reason": f"unfilled_required:{unfilled[0]['label']}"}
        
        # Click next/submit
        action = click_primary(page)
        if not action:
            return {"status": "failed", "jid": jid, "reason": "no_next_button"}
        last_action = action
    
    return {"status": "failed", "jid": jid, "reason": "max_steps"}

def fill_fields(page, fields, profile, jid):
    filled = 0
    resume_path = find_resume(jid)
    for f in fields:
        val = resolve_field(f["label"], profile)
        if val is None:
            continue
        sel = f.get("id") and f"#{f['id']}" or f.get("name") and f'[name="{f["name"]}"]'
        if not sel:
            continue
        try:
            el = page.query_selector(sel)
            if not el:
                continue
            tag = f["tag"]
            if tag == "select":
                el.select_option(val)
            elif f["type"] in ("checkbox", "radio"):
                is_checked = val.lower() in ("yes", "true", "1", "on")
                if el.is_checked() != is_checked:
                    el.click()
            elif tag == "textarea" or f["type"] == "text":
                if not f["value"]:  # only fill if empty
                    el.fill(val)
            f["_filled"] = True
            filled += 1
        except Exception:
            pass
    # Handle resume upload
    if resume_path and fields_have_resume(fields, page):
        try:
            file_input = page.query_selector('input[type="file"]')
            if file_input:
                file_input.set_input_files(resume_path)
        except Exception:
            pass
    return filled

def fields_have_resume(fields, page):
    for f in fields:
        if f["tag"] == "input" and f.get("type") == "file":
            return True
    return bool(page.query_selector('[role="dialog"] input[type="file"]'))

def find_resume(jid):
    """Find available resume PDF."""
    d = os.path.join(RESULTS, jid)
    if os.path.isdir(d):
        for f in os.listdir(d):
            if "Resume" in f and f.endswith(".pdf"):
                return os.path.join(d, f)
    # Fallback to generic from profile
    profile_path = os.path.join(os.path.expanduser("~"), ".openclaw", "generic_resume.pdf")
    if os.path.exists(profile_path):
        return profile_path
    return None

def click_primary(page):
    """Click Next, Review, Submit, or similar primary action button.
    Returns 'submit', 'next', or None."""
    return page.evaluate("""() => {
        const dlg = document.querySelector('[role="dialog"]');
        if (!dlg) return null;
        const btns = dlg.querySelectorAll('button:not([disabled])');
        const keywords = [
            ['submit application', 'submit'],
            ['submit', 'submit'],
            ['send application', 'submit'],
            ['send', 'submit'],
            ['done', 'submit'],
            ['review', 'review'],
            ['next', 'next'],
            ['continue', 'next'],
            ['save', 'next'],
        ];
        let bestAction = null, bestScore = 0, bestBtn = null;
        for (const b of btns) {
            const t = (b.textContent || '').trim().toLowerCase();
            for (const [kw, action] of keywords) {
                if (t.includes(kw) || t === kw) {
                    const score = t === kw ? 100 : 50;
                    if (score > bestScore) { bestScore = score; bestAction = action; bestBtn = b; }
                }
            }
        }
        if (bestBtn) { bestBtn.click(); return bestAction; }
        return null;
    }""")

def llm_fill_fields(page, unfilled, all_fields, profile):
    """Use LLM to resolve unknown required fields."""
    prompt = "Fill these job application fields using my data. Return JSON array.\n\n"
    prompt += f"Name: {profile.get('first_name','')} {profile.get('last_name','')}\n"
    prompt += f"Email: {profile.get('email','')}\n"
    prompt += f"Phone: {profile.get('phone','')}\n"
    ca = profile.get("common_answers", {})
    for k, v in ca.items():
        if v: prompt += f"{k}: {v}\n"
    prompt += "\nUnfilled fields:\n"
    for f in unfilled:
        opts = f" options={f['options'][:5]}" if f.get('options') else ''
        prompt += f"  label='{f['label']}' type={f['type']}{opts}\n"
    prompt += "\nReturn: [{\"label\":\"...\", \"value\":\"...\"}]"
    
    ok, out = call_gemini_node(prompt, timeout_seconds=30)
    if not ok:
        return False
    try:
        plan = json.loads(out) if isinstance(out, str) else out
        for item in plan if isinstance(plan, list) else [plan]:
            label = item.get("label", "")
            val = item.get("value", "")
            if not label or not val:
                continue
            for f in unfilled:
                if f["label"].lower().strip() == label.lower().strip():
                    sel = f.get("id") and f"#{f['id']}" or f.get("name") and f'[name="{f["name"]}"]'
                    if sel:
                        try:
                            el = page.query_selector(sel)
                            if el:
                                if f["tag"] == "select":
                                    el.select_option(val)
                                else:
                                    el.fill(val)
                                f["_filled"] = True
                        except Exception:
                            pass
        return True
    except (json.JSONDecodeError, TypeError):
        return False

# ─── External ATS handler ───────────────────────────

def handle_external(page, jid, apply_url, profile):
    page.goto(apply_url, wait_until='domcontentloaded', timeout=30000)
    time.sleep(4)
    plat = detect_platform(apply_url)
    
    # Check for already applied
    text = page.evaluate("() => document.body.innerText").lower()
    for w in ["already applied", "you have already applied", "you already applied"]:
        if w in text:
            return {"status": "already_applied", "jid": jid}
    
    # Check for login
    for w in ["sign in to view", "please sign in", "sign in to continue", "join now"]:
        if w in text:
            # Try guest/express apply
            guest = page.evaluate("""() => {
                const btns = document.querySelectorAll('button, a');
                for (const b of btns) {
                    const t = (b.textContent || '').toLowerCase();
                    if (t.includes('continue without') || t.includes('apply as guest') || t.includes('express apply')) {
                        b.click(); return true;
                    }
                }
                return false;
            }""")
            if not guest:
                return {"status": "blocked", "jid": jid, "reason": "login_required", "platform": plat}
            time.sleep(3)
    
    return fill_modal(page, jid, profile)

# ─── Main entry ─────────────────────────────────────

def main():
    args = sys.argv[1:]
    jid = None
    if "--jid" in args:
        idx = args.index("--jid")
        if idx + 1 < len(args):
            jid = args[idx + 1]
    if not jid:
        die("error", "", "missing --jid")
    
    profile = load_profile()
    conn = get_conn()
    row = conn.execute("SELECT url, title, company FROM jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        die("failed", jid, "not_found")
    
    url = row["url"]
    title = row["title"]
    company = row["company"]
    
    b, ctx = connect(timeout=30)
    if not ctx:
        die("failed", jid, "chrome_not_available")
    
    page = ctx.new_page()
    try:
        plat = detect_platform(url)
        
        if plat == "linkedin":
            result = handle_linkedin(page, jid, url, profile)
        else:
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            time.sleep(3)
            result = fill_modal(page, jid, profile)
        
        print(json.dumps(result))
    except Exception as e:
        die("failed", jid, f"error:{str(e)[:120]}")
    finally:
        try: page.close()
        except: pass
        try: b.close()
        except: pass

if __name__ == "__main__":
    main()
