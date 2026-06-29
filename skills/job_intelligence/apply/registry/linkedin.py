"""LinkedIn Easy Apply flow hook. Handles ephemeral modal in one process."""

import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from apply.common.resolve import resolution_for_fill
from apply.common.page_helpers import check_applied_signal, mark_applied
from apply.common.output import emit_status, emit_next, emit_fill_report


def _profile_answers():
    """Load profile answers without importing act.py's profile loader."""
    p = os.path.join(os.path.dirname(__file__), "..", "profile.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _select_resume(page, jid):
    """Select tailored resume on LinkedIn's resume page. Returns True if action taken."""
    return page.evaluate("""() => {
        const d = document.querySelector('[role="dialog"], dialog');
        if (!d) return false;
        const radios = d.querySelectorAll('input[type="radio"]');
        if (radios.length === 0) return false;
        // Check if the right resume is already selected
        for (const r of radios) {
            const lbl = d.querySelector('label[for="' + r.id + '"]');
            const t = lbl ? lbl.textContent.trim() : '';
            if (r.checked && t.includes('.pdf')) return 'already_selected';
        }
        // Click label for the tailored resume (triggers LinkedIn's event handlers)
        const labels = d.querySelectorAll('label');
        for (const lbl of labels) {
            const t = lbl.textContent.trim();
            if (t.includes('.pdf') && !t.startsWith('Select resume') && !t.startsWith('Deselect')) continue;
            if (t.includes('.pdf')) {
                lbl.click();
                const forId = lbl.getAttribute('for');
                if (forId) {
                    const radio = d.querySelector('#' + CSS.escape(forId));
                    if (radio) {
                        radio.dispatchEvent(new Event('change', {bubbles: true}));
                        radio.dispatchEvent(new Event('input', {bubbles: true}));
                        radio.dispatchEvent(new Event('click', {bubbles: true}));
                    }
                }
                return true;
            }
        }
        return false;
    }""")


def _get_dialog_fields(page):
    """Return dialog-scoped form fields with labels and values."""
    return page.evaluate("""() => {
        const d = document.querySelector('[role=dialog], dialog');
        if (!d) return [];
        const sel = 'input:not([type=hidden]):not([type=submit]), select, textarea, [contenteditable="true"]';
        const inputs = d.querySelectorAll(sel);
        return Array.from(inputs).filter(el => el.offsetParent !== null).map(el => {
            const lbl = d.querySelector('label[for="' + el.id + '"]');
            let label = lbl ? lbl.textContent.trim() : '';
            if (!label) {
                const parent = el.closest('div,fieldset,section');
                if (parent) {
                    const h = parent.querySelector('label, legend, strong, span');
                    if (h) label = h.textContent.trim();
                }
            }
            if (!label && el.placeholder) label = el.placeholder;
            return {
                id: el.id, tag: el.tagName, type: el.type || '',
                label: label.slice(0, 80),
                value: (el.value || '').trim(),
                isEmpty: !(el.value || '').trim() || ['select an option','select one','select...','no selection'].indexOf((el.value||'').trim().toLowerCase()) >= 0,
                required: el.required,
                options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text) : []
            };
        });
    }""")


def _find_dialog_button(page, kws):
    """Find a visible dialog button matching any keyword. Returns {text, tag, href} or None."""
    return page.evaluate(f"""() => {{
        const kws = {json.dumps(kws)};
        const d = document.querySelector('[role="dialog"], dialog');
        if (!d) return null;
        const btns = d.querySelectorAll('button, a[role="button"]');
        const visible = Array.from(btns).filter(b => b.offsetParent !== null && !b.disabled);
        for (const b of visible) {{
            const t = (b.textContent || '').trim().toLowerCase();
            for (const kw of kws) {{
                if (t === kw) {{
                    return {{text: t, tag: b.tagName, href: b.href || ''}};
                }}
            }}
        }}
        // Broader match (starts with)
        for (const b of visible) {{
            const t = (b.textContent || '').trim().toLowerCase();
            for (const kw of kws) {{
                if (t.startsWith(kw)) {{
                    return {{text: t, tag: b.tagName, href: b.href || ''}};
                }}
            }}
        }}
        return null;
    }}""")


def _click_dialog_button(page, btn_info):
    """Click a dialog button by info dict from _find_dialog_button."""
    if btn_info["tag"] == "A" and btn_info["href"]:
        page.goto(btn_info["href"], wait_until="domcontentloaded", timeout=15000)
        time.sleep(2)
        return
    text = btn_info["text"]
    try:
        loc = page.locator(f'[role="dialog"] button:has-text("{text}"), dialog button:has-text("{text}")')
        if loc.count() > 0:
            loc.first.click(timeout=10000)
            return
    except Exception:
        pass
    page.evaluate(f"""() => {{
        const d = document.querySelector('[role="dialog"], dialog');
        if (!d) return;
        const btns = d.querySelectorAll('button');
        for (const b of btns) {{
            if ((b.textContent || '').trim().toLowerCase() === '{text}') {{
                b.click(); return;
            }}
        }}
    }}""")


def easy_apply_flow(page, jid):
    """Handle one page of LinkedIn Easy Apply flow. Re-entrant — each call handles one step.
    
    Returns:
        "done" — application submitted successfully
        "paused" — needs LLM input (unfilled fields without answers). State saved.
        "failed" — can't proceed (no dialog, no buttons)
    """
    # Ensure dialog is open (poll for up to 10s — LinkedIn loads async)
    has_dialog = page.evaluate("() => !!document.querySelector('[role=dialog], dialog')")
    if not has_dialog:
        dialog_opened = False
        for _ in range(20):
            clicked = page.evaluate("""() => {
                const all = document.querySelectorAll('button, a');
                for (const el of all) {
                    if (el.offsetParent === null) continue;
                    const t = (el.textContent || '').trim().toLowerCase();
                    if (t === 'easy apply' || t.startsWith('easy apply')) {
                        el.click(); return true;
                    }
                }
                return false;
            }""")
            if clicked:
                time.sleep(1.5)
                if page.evaluate("() => !!document.querySelector('[role=dialog], dialog')"):
                    dialog_opened = True
                    break
            time.sleep(0.5)
        if not dialog_opened:
            return "failed"

    time.sleep(1.5)

    # Try resume selection
    res = _select_resume(page, jid)
    if res:
        print(f"RESUME:{jid} selected tailored resume", file=sys.stderr)
        time.sleep(1)

    # Check for success before proceeding
    if check_applied_signal(page):
        mark_applied(jid)
        return "done"

    # Detect dialog fields
    fields = _get_dialog_fields(page)
    unfilled = []

    profile = _profile_answers()
    answers = profile.get("answers", {})

    for f in fields:
        if f.get("isEmpty", True):
            lbl = f["label"]
            res = resolution_for_fill(lbl, profile, answers_override=None)
            if res and res.value:
                try:
                    if f["tag"] == "SELECT":
                        page.locator(f'#{f["id"]}').select_option(res.value)
                        print(f"  FILLED: {lbl[:40]} -> {res.value}", file=sys.stderr)
                    elif f["type"] == "radio":
                        page.locator(f'label[for="{f["id"]}"]').first.click()
                        print(f"  FILLED: {lbl[:40]} -> radio click", file=sys.stderr)
                    else:
                        page.locator(f'#{f["id"]}').fill(res.value)
                        print(f"  FILLED: {lbl[:40]} -> {res.value}", file=sys.stderr)
                except Exception as e:
                    print(f"  FILL_WARN: {lbl[:40]} — {e}", file=sys.stderr)
                    unfilled.append({"label": lbl, "options": f.get("options", []), "tag": f["tag"]})
            else:
                unfilled.append({"label": lbl, "options": f.get("options", []), "tag": f["tag"]})

    if unfilled:
        emit_fill_report(0, unfilled, "?")
        emit_next('act --fill --answers \'{"<label>": "<value>"}\'')
        return "paused"

    # Check for success
    if check_applied_signal(page):
        mark_applied(jid)
        return "done"

    # Find and click action button
    btn = _find_dialog_button(page, ["submit application", "submit", "send application",
                                     "review", "next", "continue", "done"])
    if not btn:
        if check_applied_signal(page):
            mark_applied(jid)
            return "done"
        # No buttons — modal might have closed after submit
        has_dialog = page.evaluate("() => !!document.querySelector('[role=dialog], dialog')")
        if not has_dialog:
            mark_applied(jid)
            return "done"
        return "failed"

    _click_dialog_button(page, btn)
    time.sleep(3)

    # After submit button, check for success
    if btn["text"] in ("submit application", "submit", "send application"):
        if check_applied_signal(page):
            mark_applied(jid)
            return "done"
        # If modal closed after submit, assume success
        time.sleep(1)
        has_dialog = page.evaluate("() => !!document.querySelector('[role=dialog], dialog')")
        if not has_dialog:
            mark_applied(jid)
            return "done"

    # Still advancing — next call handles the new page
    emit_status("advanced", f"clicked {btn['text']}")
    emit_next("act --fill")
    return "paused"
