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
    """Read dialog (LinkedIn modal) or document (external ATS). Returns dict."""
    return p.evaluate("""() => {
        const container = document.querySelector('[role="dialog"]') || document;
        const inputs = container.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea');
        const btns = container.querySelectorAll('button');
        const fields = Array.from(inputs).map(el => {
            const lbl = container.querySelector('label[for="' + el.id + '"]');
            const parent = el.closest('div,fieldset,section,li');
            const plbl = parent ? parent.querySelector('label, legend, strong, span') : null;
            let label = (lbl ? lbl.textContent.trim() : '') || el.placeholder || el.getAttribute('aria-label') || '';
            if (!label && plbl) label = plbl.textContent.trim();
            return {
                tag: el.tagName, type: el.getAttribute('type') || '',
                id: el.id, name: el.getAttribute('name') || '',
                label: label.replace(/\\s+/g,' ').trim().slice(0, 80),
                required: el.required, value: el.value || '',
                checked: el.type === 'radio' ? el.checked : null,
                options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean).slice(0,15) : [],
            };
        });
        return {
            fieldCount: fields.length, fields: fields.slice(0,35),
            hasFileInput: container.querySelectorAll('input[type="file"]').length > 0,
            hasRequiredFile: container.querySelectorAll('input[type="file"][required]').length > 0,
            buttons: Array.from(btns).filter(b => b.offsetParent !== null).map(b => ({
                text: (b.textContent || '').trim().slice(0,30), disabled: b.disabled
            })),
        };
    }""")

def find_page(ctx, state):
    """Find the page by external_url or LinkedIn jobs URL."""
    ext = state.get("external_url", "")
    for p in ctx.pages:
        url = p.url
        if ext and (url in ext or ext in url):
            return p
        if "linkedin.com/jobs/view" in url:
            return p
    return None

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
