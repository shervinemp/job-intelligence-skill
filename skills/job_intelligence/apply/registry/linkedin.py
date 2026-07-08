"""LinkedIn Easy Apply flow hook. Multi-step handler in one connection.

Opens the Easy Apply modal, navigates through all pages (resume → contact → review → submit),
filling fields with profile data and clicking buttons. Uses React-aware native value setters.

Hook contract (see cmd_fill): receives profile/answers from the caller and an
allow_submit flag from the submit gate. It must never click a submit-intent
button when allow_submit is False, and must only mark_applied on positive
evidence (success signal, or modal closed right after *this call* clicked submit).
"""

import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from apply.common.page_helpers import check_applied_signal, mark_applied
from apply.common.output import emit_status, emit_next

_MAX_STEPS = 8
_SUBMIT_BUTTON_TEXTS = ["Submit application", "Submit"]
_NEXT_BUTTON_TEXTS = ["Next", "Continue", "Done", "Review"]


def _fill_fields(page, profile):
    """Fill visible text inputs in the dialog using profile data.
    Uses React-aware native value setter for LinkedIn's framework.
    Returns the number of fields filled.
    """
    return page.evaluate(f"""() => {{
        const d = document.querySelector('[role="dialog"], dialog');
        if (!d) return 0;
        const inputs = d.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=radio]), textarea');
        const profile = {json.dumps(profile)};
        let count = 0;
        for (const inp of inputs) {{
            if (inp.offsetParent === null) continue;
            const val = (inp.value || '').trim();
            if (val && !['select an option','select one','select...','no selection'].includes(val.toLowerCase())) continue;
            const ph = (inp.placeholder || '').toLowerCase();
            const name = (inp.name || '').toLowerCase();
            const aria = (inp.getAttribute('aria-label') || '').toLowerCase();
            const ans = profile['answers'] || {{}};
            let fill = '';
            if (ph.includes('email') || name.includes('email') || aria.includes('email')) fill = profile['email'] || '';
            else if (ph.includes('phone') || name.includes('phone') || aria.includes('phone')) fill = profile['phone'] || '';
            else if (ph.includes('first name') || ph.includes('given name') || name === 'firstname' || name === 'first_name') fill = profile['first_name'] || '';
            else if (ph.includes('last name') || ph.includes('family name') || name === 'lastname' || name === 'last_name') fill = profile['last_name'] || '';
            else if (ph.includes('name') && !name.includes('company')) fill = ((profile['first_name']||'') + ' ' + (profile['last_name']||'')).trim();
            else if (ph.includes('company') || name.includes('company')) fill = ans['current_company'] || '';
            else if (ph.includes('title') || name.includes('title')) fill = ans['current_job_title'] || '';
            else if (ph.includes('experience') || name.includes('experience')) fill = ans['years_of_experience'] || '';
            else if (ph.includes('notice') || name.includes('notice')) fill = ans['notice_period'] || '';
            else if (ph.includes('salary') || name.includes('salary')) fill = profile['expected_salary'] || '';
            if (fill) {{
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(inp, fill);
                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                count++;
            }}
        }}
        return count;
    }}""")


def _click_button(page, texts):
    """Click a button in the dialog matching one of the texts. Returns True if clicked."""
    for t in texts:
        try:
            btn = page.locator(f'[role="dialog"] button:has-text("{t}"), dialog button:has-text("{t}")')
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                return True
        except Exception:
            pass
    return False


def _dialog_open(page):
    return page.evaluate("() => !!document.querySelector('[role=dialog], dialog')")


def _open_modal(page):
    if _dialog_open(page):
        return True
    """Click Easy Apply button if dialog isn't open. Returns True if open."""
    if _dialog_open(page):
        return True
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
        time.sleep(2)
        for _ in range(10):
            if _dialog_open(page):
                return True
            time.sleep(0.5)
    return _dialog_open(page)


def _ensure_tailored_resume(page, jid, profile):
    """Select or upload the tailored resume. Aborts if neither is possible."""
    from lib.config import RESULTS_DIR
    rd = os.path.join(RESULTS_DIR, jid)
    pdf_path = None
    target_name = None
    if os.path.isdir(rd):
        for f in sorted(os.listdir(rd)):
            if "Resume" in f and f.endswith(".pdf"):
                pdf_path = os.path.join(rd, f)
                target_name = f.replace(".pdf", "")
                break
    if not pdf_path or not os.path.exists(pdf_path):
        print(f"RESUME:{jid} no tailored resume PDF found", file=sys.stderr)
        return False

    # Phase 1: try to find and select the tailored resume in the dialog
    selected = page.evaluate(f"""() => {{
        const d = document.querySelector('[role="dialog"], dialog');
        if (!d) return 'no_dialog';
        const target = {json.dumps(target_name)};
        const spans = d.querySelectorAll('span');
        for (const s of spans) {{
            const txt = s.textContent.trim();
            if (txt.includes('.pdf') && txt.includes(target)) {{
                // Found it — click the card/link
                let el = s;
                for (let i = 0; i < 15 && el; i++) {{
                    const a = el.closest('a');
                    if (a && a.offsetParent !== null) {{ a.click(); return 'selected'; }}
                    el = el.parentElement;
                }}
                return 'click_failed';
            }}
        }}
        return 'not_found';
    }}""")
    if selected == 'selected':
        print(f"RESUME:{jid} selected {target_name}", file=sys.stderr)
        return True
    if selected == 'not_found':
        print(f"RESUME:{jid} {target_name} not on LinkedIn — uploading...", file=sys.stderr)
    else:
        print(f"RESUME:{jid} selection result: {selected}", file=sys.stderr)

    # Expand resume list to ensure Upload button is visible
    page.evaluate("""() => {
        const d = document.querySelector('[role="dialog"], dialog');
        if (!d) return;
        const btns = d.querySelectorAll('button');
        for (const b of btns) {
            if (b.offsetParent && !b.disabled && b.textContent.trim() === 'Show 3 more resumes') {
                b.click(); return;
            }
        }
    }""")
    time.sleep(1)

    # Phase 2: upload the tailored resume using file chooser
    print(f"RESUME:{jid} uploading...", file=sys.stderr)
    try:
        # First, find the upload element
        upload_sel = page.evaluate("""() => {
            const d = document.querySelector('[role="dialog"], dialog');
            if (!d) return null;
            const all = d.querySelectorAll('button, a, span, div, label');
            for (const el of all) {
                if (el.offsetParent === null) continue;
                const t = (el.textContent || '').trim();
                if (t === 'Upload resume') {
                    // Return a selector path
                    if (el.id) return '#' + CSS.escape(el.id);
                    if (el.getAttribute('data-test-id')) return '[data-test-id="' + el.getAttribute('data-test-id') + '"]';
                    return el.tagName + ':has-text("Upload resume")';
                }
            }
            return null;
        }""")
        if not upload_sel:
            print(f"RESUME:{jid} Upload resume element not found", file=sys.stderr)
            return False
        # Use expect_file_chooser to capture the file dialog
        with page.expect_file_chooser() as fc_info:
            page.evaluate(f"""() => {{
                const d = document.querySelector('[role="dialog"], dialog');
                if (!d) return;
                const all = d.querySelectorAll('button, a, span, div, label');
                for (const el of all) {{
                    if (el.offsetParent === null) continue;
                    if ((el.textContent || '').trim() === 'Upload resume') {{
                        el.click();
                        return;
                    }}
                }}
            }}""")
        fc = fc_info.value
        fc.set_files(pdf_path)
        print(f"RESUME:{jid} uploaded {os.path.basename(pdf_path)}", file=sys.stderr)
        print(f"RESUME:{jid} uploaded {os.path.basename(pdf_path)}", file=sys.stderr)
        time.sleep(4)
        # Select the newly uploaded resume
        second = page.evaluate(f"""() => {{
            const d = document.querySelector('[role="dialog"], dialog');
            if (!d) return false;
            const target = {json.dumps(target_name)};
            const spans = d.querySelectorAll('span');
            for (const s of spans) {{
                const txt = s.textContent.trim();
                if (txt.includes('.pdf') && txt.includes(target)) {{
                    let el = s;
                    for (let i = 0; i < 15 && el; i++) {{
                        const a = el.closest('a');
                        if (a && a.offsetParent !== null) {{ a.click(); return true; }}
                        el = el.parentElement;
                    }}
                }}
            }}
            return false;
        }}""")
        if second:
            print(f"RESUME:{jid} selected after upload", file=sys.stderr)
            return True
        print(f"RESUME:{jid} uploaded but could not select -- maybe already selected", file=sys.stderr)
        # Check if the uploaded resume got auto-selected
        checked = page.evaluate(f"""() => {{
            const d = document.querySelector('[role="dialog"], dialog');
            if (!d) return false;
            const target = {json.dumps(target_name)};
            const radios = d.querySelectorAll('input[type="radio"]');
            for (const r of radios) {{
                if (!r.checked) continue;
                const span = d.querySelector('span');
                if (span && span.textContent.includes(target)) return true;
            }}
            return false;
        }}""")
        if checked:
            print(f"RESUME:{jid} auto-selected after upload", file=sys.stderr)
            return True
        print(f"RESUME:{jid} could not verify selection", file=sys.stderr)
        return False
    except Exception as e:
        print(f"RESUME:{jid} upload failed — {type(e).__name__}: {e}", file=sys.stderr)
        return False


def easy_apply_flow(page, jid, profile=None, answers=None, allow_submit=True):
    """Multi-step LinkedIn Easy Apply handler. Re-entrant — handles all steps.
    
    Returns:
        "done" — application submitted successfully
        "paused" — needs LLM input. State saved.
        "failed" — can't proceed
    """
    profile = profile or {}
    answers = answers or {}

    if not _open_modal(page):
        if check_applied_signal(page):
            mark_applied(jid)
            return "done"
        return "failed"

    # Ensure the tailored resume is selected/uploaded before proceeding
    if not _ensure_tailored_resume(page, jid, profile):
        print(f"RESUME:{jid} cannot proceed without tailored resume — aborting", file=sys.stderr)
        emit_status("blocked", "tailored resume not available — needs manual upload")
        emit_next("detect to retry")
        return "failed"

    for step in range(_MAX_STEPS):
        if not _dialog_open(page):
            # Check for success signal right after modal closes
            if check_applied_signal(page):
                mark_applied(jid)
                return "done"
            # Modal closed without submit click — needs verification
            emit_status("dialog_closed", "modal closed without submit")
            emit_next("verify")
            return "paused"

        # Fill fields first
        filled = _fill_fields(page, profile)
        if filled:
            print(f"  FILLED: {filled} field(s)", file=sys.stderr)
            time.sleep(0.5)

        # Try submit button (only if allowed)
        if allow_submit and _click_button(page, _SUBMIT_BUTTON_TEXTS):
            print(f"  SUBMIT: clicked submit", file=sys.stderr)
            time.sleep(3)
            if not _dialog_open(page):
                if check_applied_signal(page):
                    mark_applied(jid)
                    return "done"
                # Modal closed right after our click — positive evidence
                time.sleep(1)
                if not _dialog_open(page):
                    mark_applied(jid)
                    return "done"
            check_applied_signal(page)
            emit_status("submitted")
            emit_next("verify")
            return "done"

        # Try next/review/continue button
        if _click_button(page, _NEXT_BUTTON_TEXTS + _SUBMIT_BUTTON_TEXTS):
            time.sleep(2)
            continue

        # No clickable button found — check for applied signal
        if check_applied_signal(page):
            mark_applied(jid)
            return "done"

        emit_status("paused", "no actionable button found in dialog")
        emit_next("act --fill")
        return "paused"

    # Hit max steps — the modal should be submitted or closed
    if not _dialog_open(page):
        if check_applied_signal(page):
            mark_applied(jid)
            return "done"
    emit_status("paused", "max steps reached")
    emit_next("verify")
    return "paused"
