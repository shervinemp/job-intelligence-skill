"""apply/common/page_helpers.py — Shared page reading, state persistence, page finding."""
import json, os, re, time

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "apply_state.json")

def load_state():
    with open(STATE_PATH) as f:
        return json.load(f)

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

def read_page(p):
    """Read page content including fields, buttons, page type hints.
    Queries document-level (works for both modals and external ATS)."""
    result = p.evaluate("""() => {
        const inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
        const dropdowns = document.querySelectorAll('button[aria-haspopup="listbox"]');
        const btns = document.querySelectorAll('button');
        const fields = Array.from(inputs).map(el => {
            const lbl = document.querySelector('label[for="' + el.id + '"]');
            const parentLabel = el.closest('label');
            const parent = el.closest('div,fieldset,section,li,form');
            const plbl = parent ? parent.querySelector('label, legend, strong, span') : null;
            let label = (lbl ? lbl.textContent.trim() : '') || el.placeholder || el.getAttribute('aria-label') || '';
            if (!label && parentLabel) label = parentLabel.textContent.trim();
            if (!label && plbl) label = plbl.textContent.trim();
            return {
                tag: el.tagName, type: el.getAttribute('type') || '',
                id: el.id, name: el.getAttribute('name') || '',
                label: (label || '').replace(/\\s+/g,' ').trim().slice(0, 80),
                required: !!el.required, value: el.value || '',
                checked: el.type === 'radio' ? el.checked : null,
                options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean).slice(0,15) : [],
            };
        });
        // Custom dropdown buttons inside formField containers (e.g. Workday province/phone type)
        Array.from(dropdowns).forEach(btn => {
            const parent = btn.closest('[data-automation-id^="formField"]');
            if (!parent) return;  // skip nav-level dropdowns
            const label = parent.querySelector('label, legend, span');
            const lbl = label ? label.textContent.trim().replace(/\\s+/g,' ').slice(0, 80) : '';
            const current = (btn.textContent || '').trim().slice(0, 30);
            fields.push({
                tag: 'DROPDOWN', type: 'custom',
                id: btn.id, name: btn.getAttribute('name') || '',
                label: lbl, required: (lbl || '').includes('*'),
                value: current, checked: null,
                options: [],
            });
        });
        const text = (document.body.innerText || '').toLowerCase();
        const hasFormWords = text.includes('submit') || text.includes('apply') || text.includes('application');
        const hasPassword = document.querySelector('input[type="password"]') !== null;
        const isShort = (document.body.innerText || '').length < 500;
        let pageType = 'unknown';
        if (fields.length > 0) pageType = 'form';
        else if (hasPassword && (text.includes('sign in') || text.includes('log in'))) pageType = 'login_wall';
        else if (isShort && text.includes('sign in') && !text.includes('apply')) pageType = 'login_wall';
        else if (hasFormWords) pageType = 'maybe_form';
        return {
            fieldCount: fields.length,
            fields: fields.slice(0, 35),
            pageType: pageType,
            hasFileInput: document.querySelectorAll('input[type="file"]').length > 0,
            hasRequiredFile: document.querySelectorAll('input[type="file"][required]').length > 0,
            buttons: Array.from(btns).filter(b => b.offsetParent !== null).map(b => ({
                text: (b.textContent || '').trim().slice(0, 30),
                disabled: b.disabled
            })),
        };
    }""")
    return result

def find_page(ctx, state):
    """Find the best matching page by JID tag, then URL score."""
    jid = state.get("jid", "")
    ext = state.get("external_url", "").rstrip("/")

    # First pass: find by JID tag on body
    for p in ctx.pages:
        try:
            tag = p.evaluate("() => document.body.getAttribute('data-job-id') || ''")
            if tag == jid:
                return p
        except:
            pass

    # Second pass: score by URL match quality
    if ext:
        best_score = -1
        best_page = None
        for p in ctx.pages:
            url = p.url.rstrip("/")
            score = -1
            if url == ext:
                score = 3
            elif url.startswith(ext + "/"):
                score = 2
            elif ext.startswith(url + "/"):
                score = 1
            if score > best_score:
                best_score = score
                best_page = p
        if best_page:
            return best_page

    # Third pass: LinkedIn job ID match
    li_job_id = None
    if "linkedin.com/jobs/view" in ext:
        try:
            li_job_id = ext.split("/jobs/view/")[1].split("/")[0]
        except:
            pass
    if li_job_id:
        for p in ctx.pages:
            if li_job_id in p.url:
                return p

    return None

def tag_page(page, jid):
    """Tag a page with a job ID for reliable find_page lookups."""
    try:
        page.evaluate(f"(jid) => document.body.setAttribute('data-job-id', jid)", jid)
    except:
        pass

def read_and_save(p, state):
    """Read page state, save to state file, return page dict."""
    ps = read_page(p)
    state["page"] = ps
    save_state(state)
    return ps

def resolve_label(label, profile):
    """Resolve a label to a profile value. Name + email only. Returns None if uncertain."""
    norm = re.sub(r'[^a-z0-9]+', ' ', label.lower()).strip()
    if norm in ("full name"):
        fn, ln = profile.get("first_name", ""), profile.get("last_name", "")
        return f"{fn} {ln}" if fn and ln else fn or ln or None
    if norm in ("email", "email address"):
        return profile.get("email")
    return None

def scan_actions(page, keywords, exclude=None):
    """Score all clickable elements (buttons + links) against keyword list.
    Returns sorted list of candidates with scores."""
    exclude = exclude or {"back", "cancel", "save", "edit", "delete", "remove", "upload", "browse"}
    result = page.evaluate("""((args) => {
        const kws = args[0], excl = new Set(args[1].map(e => e.toLowerCase()));
        const currentUrl = location.href.replace(/\\/$/, '').toLowerCase();
        const all = document.querySelectorAll('button, a');
        const candidates = [];
        for (const el of all) {
            if (el.offsetParent === null) continue;
            const text = (el.textContent || '').trim().toLowerCase();
            if (excl.has(text)) continue;
            const href = (el.href || '').toLowerCase().replace(/\\/$/, '');
            // Skip self-referencing links
            if (el.tagName === 'A' && href === currentUrl) continue;
            let score = 0;
            for (const kw of kws) {
                if (text === kw) score = Math.max(score, 4);
                else if (text.startsWith(kw)) score = Math.max(score, 3);
                else if (text.includes(kw)) score = Math.max(score, 2);
                else if (href.includes(kw)) score = Math.max(score, 1);
            }
            if (score > 0) {
                candidates.push({
                    text: text.slice(0, 30), score: score, tag: el.tagName,
                    href: href,
                    disabled: el.disabled || false
                });
            }
        }
        candidates.sort((a, b) => b.score - a.score);
        return candidates;
    })""", [keywords, list(exclude)])
    return result
