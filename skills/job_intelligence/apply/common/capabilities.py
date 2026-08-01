"""capabilities.py — Cheap capability scan (single page.evaluate).

Profiles a page's form-shape signals in one pass for routing the probe
cascade. Pure observation — never modifies the page. Returns a
CapabilityProfile dict that can be hashed to group similar pages
(workday-like, ashby-like, etc.) without domain knowledge.

The profile is the *only* learned key. The probe router uses it to:
  1. Pick a preferred starting strategy when no YAML matches
  2. Record observations keyed by profile hash so similar platforms
     share learned starting points
  3. Detect drift between runs (same profile hash but the prior
     winning strategy now returns 0 fields)

Capabilities are intentionally coarse booleans/counts, not free-form
strings, so the hash is stable across minor DOM changes (e.g., class
names added, IDs regenerated).
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional, TypedDict


class CapabilityProfile(TypedDict, total=False):
    dialog: bool
    nested_dialog: bool
    iframes: int
    cross_origin_iframes: int
    shadow_roots: int
    comboboxes: int
    listboxes: int
    listbox_buttons: int            # button[aria-haspopup='listbox']
    file_inputs: int
    password_fields: int
    email_fields: int
    visible_text_inputs: int
    select_elements: int
    textarea_count: int
    radio_groups: int
    checkbox_count: int
    apply_buttons: list[str]        # visible button text snippets
    submit_buttons: list[str]
    login_signals: list[str]         # body text tokens that suggest login wall
    eeoc_signals: list[str]         # body text tokens suggesting EEOC page
    has_progress_bar: bool
    has_captcha: bool
    honeypot_signals: int
    page_text_length: int           # body.innerText length (lower bound)
    confirm_modal_signals: int      # visible HTML modals with confirm/ok button text
    success_modal_text: bool        # body text suggests "submitted successfully"
    error_modal_text: bool          # body text shows validation error in a modal
    dropzone_signals: int           # dropzone.js / drag-drop file upload areas
    calendar_signals: int           # jQuery UI / React-Day-Picker calendar widgets
    two_factor_signals: int         # 6-digit 2FA / one-time-code input pattern


_SCAN_JS = r"""() => {
    const body = document.body || {};
    const txt = (body.innerText || '').toLowerCase();
    const slice = (s, n) => (s || '').trim().slice(0, n);

    // Visible-element counters
    function visible(sel, root) {
        root = root || document;
        let n = 0;
        root.querySelectorAll(sel).forEach(el => { if (el.offsetParent !== null) n++; });
        return n;
    }

    function visibleTexts(sel, n, root) {
        root = root || document;
        const out = [];
        root.querySelectorAll(sel).forEach(el => {
            if (el.offsetParent === null) return;
            const t = (el.textContent || '').trim();
            if (t) out.push(slice(t, n));
        });
        return out;
    }

    // Dialog detection: open modal that hosts a form. Nested-dialog
    // signals (inner dialog inside outer) catch layered SPAs.
    const dialogs = document.querySelectorAll('[role="dialog"], dialog');
    let visibleDialogs = 0;
    let nestedDialog = false;
    dialogs.forEach(d => {
        if (d.offsetParent === null) return;
        visibleDialogs++;
        if (d.querySelector('[role="dialog"], dialog')) nestedDialog = true;
    });

    // Iframes (count + best-effort cross-origin detection by src)
    let iframes = 0;
    let crossOrigin = 0;
    document.querySelectorAll('iframe').forEach(f => {
        if (f.offsetParent === null && f !== document.body) return;
        iframes++;
        const src = f.src || f.getAttribute('data-src') || '';
        if (src) {
            try {
                const u = new URL(src, location.href);
                if (u.origin !== location.origin) crossOrigin++;
            } catch (e) {}
        }
    });

    // Shadow roots (visible hosts only — heavy selector skipped for
    // performance; we count hosts not inputs)
    let shadowHosts = 0;
    document.querySelectorAll('*').forEach(el => {
        if (el.shadowRoot && el.offsetParent !== null) shadowHosts++;
    });

    // Widget counters — covers both native and ARIA variants
    const comboboxes = visible('[role="combobox"]')
                     + visible('input[role="combobox"]');
    const listboxes = visible('[role="listbox"]');
    const listboxButtons = visible("button[aria-haspopup='listbox'], button[aria-haspopup='listbox ']");
    const selectEls = visible('select');
    const fileInputs = visible('input[type="file"]');
    const passwordFields = visible('input[type="password"]');
    const emailFields = visible('input[type="email"]')
                      + visible('input[name*="email" i]');
    const visibleTextInputs = visible('input[type="text"], input:not([type])')
                           + visible('input[type="tel"], input[type="number"], input[type="url"]');
    const textareas = visible('textarea');

    // Radio groups (group by name; count distinct groups, not single
    // radio inputs, to mirror field_reader semantics)
    const radioNames = new Set();
    document.querySelectorAll('input[type="radio"]').forEach(r => {
        if (r.offsetParent !== null && r.name) radioNames.add(r.name);
    });
    const radioGroups = radioNames.size;

    const checkboxes = visible('input[type="checkbox"]');

    // Buttons — capture text snippets for apply / submit detection
    const applyButtons = [];
    const submitButtons = [];
    document.querySelectorAll('button, input[type="submit"], [role="button"]').forEach(b => {
        if (b.offsetParent === null) return;
        const t = (b.textContent || b.value || '').trim().toLowerCase();
        if (!t) return;
        if (/^(apply now|apply|easy apply|continue|start application|submit application|submit)$/.test(t)) {
            applyButtons.push(slice(t, 40));
        }
        if (/submit|next|continue|review|sign in|create account|save/.test(t)) {
            if (submitButtons.length < 8) submitButtons.push(slice(t, 40));
        }
    });

    // Login-wall signals (cheap text match — registry's pattern list
    // remains authoritative for confirmed platforms)
    const loginSignals = [];
    const _L = ['sign in to apply', 'sign in with email', 'log in to apply',
                'please sign in', 'sign in or', 'create an account',
                'create account', 'continue with email', 'login to continue'];
    for (const s of _L) {
        if (txt.includes(s)) loginSignals.push(s);
    }

    // EEOC signals (cheap heuristic — detects "self-identify" pages,
    // veterans/disability questions, etc.)
    const eeocSignals = [];
    const _E = ['voluntary self-identification', 'equal employment opportunity',
                'gender identity', 'veteran status', 'do you have a disability',
                'race/ethnicity', 'please identify your race', 'are you hispanic'];
    for (const s of _E) {
        if (txt.includes(s)) { eeocSignals.push(s); if (eeocSignals.length >= 4) break; }
    }

    // Progress bar (multi-page indicator)
    const hasProgressBar = !!document.querySelector(
        '[role="progressbar"], .progress, [data-automation-id="progressBar"], [aria-valuenow]');
    // Visible-only check on progressbar:
    let pbVisible = false;
    document.querySelectorAll('[role="progressbar"], [data-automation-id="progressBar"]')
        .forEach(el => { if (el.offsetParent !== null) pbVisible = true; });

    // CAPTCHA heuristics — detect visible reCAPTCHA / hCaptcha iframes
    let hasCaptcha = false;
    document.querySelectorAll('iframe').forEach(f => {
        if (f.offsetParent === null) return;
        const src = (f.src || '').toLowerCase();
        if (src.includes('recaptcha') || src.includes('hcaptcha') || src.includes('captcha')) {
            hasCaptcha = true;
        }
    });

    // Honeypot signals — hidden text inputs without required (catches
    // Workday-style "website" robots-only fields). Cheap because we
    // only scan <input type="text"> siblings.
    let honeypot = 0;
    document.querySelectorAll('input[type="text"], input:not([type])').forEach(el => {
        const ariaHidden = el.getAttribute('aria-hidden') === 'true'
                        || (el.parentElement && el.parentElement.getAttribute('aria-hidden') === 'true');
        const offscreen = el.offsetParent === null
                       && el.getAttribute('tabindex') === '-1'
                       && !el.required;
        if (ariaHidden || offscreen) honeypot++;
    });

    // Confirm modal detection — visible HTML modals that have an
    // "OK" / "Confirm" / "Submit" / "Continue" button. These are
    // mid-fill popups ("Please confirm your email") or leave-page
    // warnings that need dismissal before the form can be touched.
    // Distinguishes from form dialogs (which have inputs the user
    // must fill, not just OK/Cancel buttons).
    const confirmKws = ['ok', 'confirm', 'continue', 'submit', 'yes',
                        'sure', 'i agree', 'dismiss', 'got it', 'close',
                        'leave anyway', 'stay on page',
                        'accept', 'accept all', 'agree', 'allow',
                        'allow all', 'accept and continue'];
    let confirmModalCount = 0;
    dialogs.forEach(d => {
        if (d.offsetParent === null) return;
        if (d.querySelector('input:not([type=hidden]):not([type=submit]), select, textarea')) {
            // Has form fields → this is a form dialog, not confirm modal
            return;
        }
        const btns = d.querySelectorAll('button, [role="button"], input[type="submit"]');
        for (const btn of btns) {
            const t = (btn.textContent || btn.value || '').trim().toLowerCase();
            if (confirmKws.some(k => t === k || t.startsWith(k))) {
                confirmModalCount++;
                break;
            }
        }
    });

    // Success modal — body text inside a visible dialog says "submitted"
    // or "successfully" or "we'll be in touch". Detects post-submit
    // confirmation so the orchestrator knows submission already happened.
    let successModalText = false;
    dialogs.forEach(d => {
        if (d.offsetParent === null) return;
        const t = (d.innerText || '').toLowerCase();
        if (/submission successful|application submitted|successfully submitted|we'\''ll be in touch|thank you for applying/.test(t)) {
            successModalText = true;
        }
    });

    // Error modal — validation error inside visible dialog (not form
    // field error, but a modal popup saying "please fix errors above").
    // Useful for cascading: the presence of an error modal means the
    // outer form has unmet validation requirements.
    let errorModalText = false;
    dialogs.forEach(d => {
        if (d.offsetParent === null) return;
        const t = (d.innerText || '').toLowerCase();
        if (/please (fix|correct|review)|fix the following|some information is missing|please complete all (required )?fields/.test(t)) {
            errorModalText = true;
        }
    });

    // Dropzone / drag-and-drop file upload detection. These widgets
    // use a styled container (NOT an <input type=file>) where the
    // user drags files onto a region. _try_filechooser_upload in
    // helpers.py won't fire because there's no upload button —
    // detection here surfaces the pattern so fillers can synth a
    // DataTransfer event (future work).
    let dropzoneCount = 0;
    document.querySelectorAll(
        '.dropzone, [class*="dropzone"], [data-dropzone], '
        + '[class*="drop-zone"], [class*="filedrop"], '
        + '[role="button"][aria-label*="drop" i], '
        + 'div[ondragover], div[ondrop]'
    ).forEach(el => {
        if (el.offsetParent === null) return;
        // Filter out obvious false positives — anything with text
        // inputs is a form, not a dropzone.
        if (el.querySelector('input:not([type=file]):not([type=hidden])')) return;
        dropzoneCount++;
    });

    // Calendar widget detection — jQuery UI datepicker, React-Day-
    // Picker, Pikaday, etc. These pop up a calendar grid (table with
    // class containing "calendar" or "datepicker") when a text input
    // is focused. Defensive — the existing DatepickerFiller handles
    // inputs after the calendar is open, but the presence signal lets
    // the probe router prefer the standard depth (calendars re-render
    // inputs, so document-level probing works).
    let calendarCount = 0;
    document.querySelectorAll(
        '.ui-datepicker-calendar, .ui-datepicker, '
        + '[class*="DayPicker"][class*="Month"], '
        + '.pika-single, .pika-table, '
        + '[class*="calendar"][class*="day"], '
        + '[data-automation-id="calendar"], '
        + '[role="grid"][class*="calendar"]'
    ).forEach(el => {
        if (el.offsetParent === null) return;
        calendarCount++;
    });

    // Two-factor auth interstitial — 6-digit numeric code input,
    // OR one-time-code autocomplete. Detected here even though
    // _login_check also does — the scan-time signal lets the probe
    // router skip probing since this page is not a form (it's a 2FA
    // interstitial between login and the apply form).
    let twoFactorCount = 0;
    document.querySelectorAll(
        'input[autocomplete*="one-time-code" i],'
        + 'input[inputmode="numeric"][maxlength],'
        + 'input[type="tel"][maxlength]'
    ).forEach(inp => {
        if (inp.offsetParent === null) return;
        const ml = parseInt(inp.getAttribute('maxlength') || '0', 10);
        const ac = inp.getAttribute('autocomplete') || '';
        if ((ml >= 4 && ml <= 8) || /one-time-code/i.test(ac)) {
            twoFactorCount++;
        }
    });

    return {
        dialog: visibleDialogs > 0,
        nested_dialog: nestedDialog,
        iframes: iframes,
        cross_origin_iframes: crossOrigin,
        shadow_roots: shadowHosts,
        comboboxes: comboboxes,
        listboxes: listboxes,
        listbox_buttons: listboxButtons,
        file_inputs: fileInputs,
        password_fields: passwordFields,
        email_fields: emailFields,
        visible_text_inputs: visibleTextInputs,
        select_elements: selectEls,
        textarea_count: textareas,
        radio_groups: radioGroups,
        checkbox_count: checkboxes,
        apply_buttons: applyButtons,
        submit_buttons: submitButtons,
        login_signals: loginSignals,
        eeoc_signals: eeocSignals,
        has_progress_bar: pbVisible,
        has_captcha: hasCaptcha,
        honeypot_signals: honeypot,
        confirm_modal_signals: confirmModalCount,
        success_modal_text: successModalText,
        error_modal_text: errorModalText,
        dropzone_signals: dropzoneCount,
        calendar_signals: calendarCount,
        two_factor_signals: twoFactorCount,
        page_text_length: txt.length,
    };
}"""


# Capabilities we treat as "strong signal" for suggesting a starting
# probe strategy. Each maps to a probe depth that has a high chance of
# holding the real form when the signal fires.
_CAPABILITY_TO_STRATEGY = [
    ("dialog",           "dialog"),
    ("nested_dialog",    "dialog"),
    ("cross_origin_iframes", "iframe_navigate"),
    ("iframes",          "iframe"),
    ("shadow_roots",     "shadow_dom"),
    ("listbox_buttons",  "custom_widgets"),
    ("comboboxes",       "custom_widgets"),
    ("login_signals",    "standard"),   # login page — fields are top-level
]


def _hash_profile(profile: dict) -> str:
    """Stable hash of a capability profile.

    Excludes volatile counts (apply_buttons text, submit_buttons text,
    page_text_length) so minor copy changes don't rotate the key. Keeps
    booleans and bucketed counts — same shape → same key.
    """
    stable_keys = [
        "dialog", "nested_dialog", "iframes", "cross_origin_iframes",
        "shadow_roots", "comboboxes", "listboxes", "listbox_buttons",
        "file_inputs", "password_fields", "email_fields",
        "visible_text_inputs", "select_elements", "textarea_count",
        "radio_groups", "checkbox_count", "has_progress_bar",
        "has_captcha", "honeypot_signals",
        # Modal-popup counts are structural (a platform that shows
        # confirm modals reliably should hash consistently). Success/error
        # modal text is copy-driven volatile, so excluded.
        "confirm_modal_signals",
        # Dropzone / calendar / 2FA interstitial presence are
        # structural — a platform that uses dropzone.js reliably
        # should hash distinctly from one that uses <input type=file>.
        "dropzone_signals", "calendar_signals", "two_factor_signals",
    ]
    # Bucket counts: 0 → 0, 1-2 → 1, 3-10 → 2, 11+ → 3 — stable across
    # small form-size changes (a 5-question form vs a 7-question one
    # on the same SPA both hash to bucket 2 for visible_text_inputs).
    def bucket(n):
        if n == 0: return 0
        if n <= 2: return 1
        if n <= 10: return 2
        return 3

    stable = {k: bucket(profile.get(k, 0)) if isinstance(profile.get(k, 0), int)
              else bool(profile.get(k, False))
              for k in stable_keys}
    # Login + EEOC signal presence is part of the profile hash (binary)
    stable["has_login_signals"] = bool(profile.get("login_signals"))
    stable["has_eeoc_signals"] = bool(profile.get("eeoc_signals"))
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def scan(page) -> Optional[CapabilityProfile]:
    """Run the capability scan. Returns CapabilityProfile dict or
    None on hard failure (page detached, navigation racing)."""
    try:
        result = page.evaluate(_SCAN_JS)
        if not isinstance(result, dict):
            return None
        return result
    except Exception:
        return None


def profile_hash(profile: Optional[dict]) -> str:
    """Compute the stable hash for a capability profile."""
    if not profile:
        return "unknown"
    return _hash_profile(profile)


def suggest_strategy(profile: Optional[dict]) -> Optional[str]:
    """Best-effort suggestion of which probe depth to try first.

    Uses the _CAPABILITY_TO_STRATEGY table (single source of truth for
    capability→strategy mapping), checked in table order (strongest
    signal first). Returns None if no signal fires (let the full
    cascade run in declaration order). The caller always runs the
    cascade as a backstop regardless.

    Special-case: 2FA / success-modal pages aren't forms to probe —
    return None so the probe caller can short-circuit via
    `_scan_capability` + caller decision before even probing.
    """
    if not profile:
        return None
    # 2FA / success modal — not a form, no probe needed.
    # Caller should check these before calling probe().
    if profile.get("two_factor_signals") or profile.get("success_modal_text"):
        return None
    for cap, strategy in _CAPABILITY_TO_STRATEGY:
        if profile.get(cap):
            return strategy
    return None


def discover_widgets(profile: Optional[dict], page) -> dict:
    """Auto-discover custom widget CSS selectors from ARIA patterns.

    Returns a {widget_type: css_selector} dict suitable for passing to
    read_fields(custom_widgets=...). Used as a fallback when a
    platform's registry YAML doesn't supply widget selectors.

    Stable, ARIA-based discovery — works on any modern ATS that
    follows accessibility patterns. False positives are filtered at
    the probe layer (custom_widgets strategy returns 0 fields when
    the selector matches no form-control-like elements).
    """
    if not profile:
        return {}
    widgets = {}
    # Listbox-backed buttons (Workday, Ashby) — the form control
    if profile.get("listbox_buttons"):
        widgets["dropdown"] = "button[aria-haspopup='listbox']"
    # ARIA combobox inputs (Greenhouse variants, Lever custom widgets)
    if profile.get("comboboxes"):
        widgets.setdefault("autocomplete", "input[role='combobox']")
    # Native combobox wrapper div with role=combobox (SAP-SF, some Workday variants)
    if profile.get("comboboxes") and not widgets.get("combobox"):
        widgets["combobox"] = "[role='combobox']"
    return widgets


def summarize(profile: Optional[dict]) -> str:
    """Short human-readable summary for diagnostics."""
    if not profile:
        return "no-profile"
    parts = []
    if profile.get("dialog"): parts.append("dialog")
    if profile.get("nested_dialog"): parts.append("nested-dialog")
    if profile.get("iframes"): parts.append(f"iframe:{profile['iframes']}")
    if profile.get("cross_origin_iframes"):
        parts.append(f"xorigin-iframe:{profile['cross_origin_iframes']}")
    if profile.get("shadow_roots"): parts.append(f"shadow:{profile['shadow_roots']}")
    if profile.get("comboboxes"): parts.append(f"combobox:{profile['comboboxes']}")
    if profile.get("listboxes"): parts.append(f"listbox:{profile['listboxes']}")
    if profile.get("listbox_buttons"): parts.append(f"listbox-btn:{profile['listbox_buttons']}")
    if profile.get("file_inputs"): parts.append(f"file:{profile['file_inputs']}")
    if profile.get("password_fields"): parts.append(f"pwd:{profile['password_fields']}")
    if profile.get("radio_groups"): parts.append(f"radio:{profile['radio_groups']}")
    if profile.get("login_signals"): parts.append("login-wall")
    if profile.get("eeoc_signals"): parts.append("eeoc")
    if profile.get("has_progress_bar"): parts.append("progress-bar")
    if profile.get("has_captcha"): parts.append("captcha")
    if profile.get("honeypot_signals"): parts.append(f"honeypot:{profile['honeypot_signals']}")
    if profile.get("confirm_modal_signals"): parts.append(f"confirm-modal:{profile['confirm_modal_signals']}")
    if profile.get("success_modal_text"): parts.append("success-modal")
    if profile.get("error_modal_text"): parts.append("error-modal")
    if profile.get("dropzone_signals"): parts.append(f"dropzone:{profile['dropzone_signals']}")
    if profile.get("calendar_signals"): parts.append(f"calendar:{profile['calendar_signals']}")
    if profile.get("two_factor_signals"): parts.append(f"2fa:{profile['two_factor_signals']}")
    if profile.get("apply_buttons"):
        joined = "','".join(profile['apply_buttons'][:2])
        parts.append("apply:['" + joined + "']")
    return " ".join(parts) if parts else "empty"