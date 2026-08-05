"""lib/linkedin_messaging.py — LinkedIn DM and connection request automation.

Uses the shared Chrome lifecycle (chrome_manager) to automate LinkedIn
messaging and connection requests via the persistent browser session.

VERIFIED against live LinkedIn DOM (2026-07):
  - People page cards:      li.org-people-profile-card__profile-card-spacing
  - Name link (prefer):     .artdeco-entity-lockup__title a[href*="/in/"]
  - Role:                   .artdeco-entity-lockup__subtitle
  - Degree:                 .artdeco-entity-lockup__degree
  - People page pagination: button[aria-label="Page N"] (not infinite scroll)
  - Recipient typeahead:    input.msg-connections-typeahead__search-field
  - Suggestion item:        li.msg-connections-typeahead__search-result
                            (first is auto-highlighted; needs REAL mouse
                             events — JS .click()/Enter do NOT register)
  - Message box:            div.msg-form__contenteditable ("Write a message…")
  - Send button:            button.msg-form__send-btn[type="submit"]
                            (disabled until text; NO aria-label)
  - InMail composer (2nd/3rd): .msg-inmail-credits-display banner +
                            input[name="subject"] — premium signal
  - Profile Message link:   a[href*="/messaging/compose/"] (text "Message")
  - Profile Connect link:   a[href*="/preload/custom-invite/"] (text "Connect")
  - URL-param recipient (?recipientUrn=...) does NOT resolve the recipient —
    the typeahead flow is the only working path.
  - Connect modal: navigating DIRECTLY to
    /preload/custom-invite/?vanityName={username} renders the modal as a
    page (no click needed). "Add a note" → textarea[name="message"] →
    button[aria-label="Send invitation"] (enables after typing).
    Send click is the one unverified step (no live send test performed);
    outcome detection = error-scan + modal-gone → uncertain otherwise.

DM flow (verified end-to-end, without sending):
  1. Open /messaging/thread/new/
  2. Click + keyboard-type the contact NAME into the recipient typeahead
  3. Wait for suggestions; mouse-click the best match (name match first)
  4. Composer appears (free for 1st degree, InMail for 2nd/3rd)
  5. Fill div.msg-form__contenteditable
  6. Click button.msg-form__send-btn (enables after typing)
"""

import json
import re
import sys
import time
from urllib.parse import urlparse

_MESSAGING_URL = "https://www.linkedin.com/messaging/thread/new/"

# ---------------------------------------------------------------------------
# JS probes (verified selectors)
# ---------------------------------------------------------------------------

_DM_CAPABILITY_JS = """() => {
  // Profile header actions (new layout: anchors with text, no aria-label)
  const msgLink = document.querySelector('a[href*="/messaging/compose/"]');
  const connectLink = document.querySelector('a[href*="/preload/custom-invite/"], a[href*="/connect/"]');
  const composeBox = document.querySelector('div[role="textbox"][aria-label*="message" i], div.msg-form__contenteditable');
  const errorMsg = document.querySelector('.msg-form__error, [data-test-error]');
  const sendBtn = document.querySelector('button.msg-form__send-btn, button[aria-label="Send"], button.msg-form__send-button');
  const signIn = document.body.innerText.includes('Sign in') ? 'sign_in' : '';
  // Premium/InMail signals — scoped, NOT the global nav (which always has a
  // "Try Premium" link for free accounts).
  const inmailBanner = document.querySelector('.msg-inmail-credits-display');
  const profileActions = document.querySelector('.pv-top-card-v2__actions, section[data-view-name="profile-actions"]');
  const inmailAction = profileActions
    ? profileActions.querySelector('a[aria-label*="InMail"], button[aria-label*="InMail"]')
    : null;
  return {
    hasMessageButton: !!(msgLink && msgLink.offsetParent !== null),
    hasConnectButton: !!(connectLink && connectLink.offsetParent !== null),
    hasInmailOption: !!inmailBanner || !!inmailAction,
    inmailComposer: !!inmailBanner,
    hasComposeBox: !!composeBox,
    sendButtonEnabled: sendBtn ? !sendBtn.disabled : false,
    hasError: !!errorMsg,
    errorText: errorMsg ? (errorMsg.textContent || '').trim() : '',
    authState: signIn
  };
}"""

_MESSAGE_SENT_JS = """() => {
  const conversationList = document.querySelector('.msg-conversations-container__conversations-list');
  const newMessage = conversationList ? conversationList.querySelector('.msg-conversation-card--is-new') : null;
  const sendButton = document.querySelector('button.msg-form__send-btn, button.msg-form__send-button, button[aria-label="Send"]');
  const errorMsg = document.querySelector('.msg-form__form__errors, .msg-form__error, [data-test-error]');
  const sentConfirmation = document.querySelector('[data-message-sent="true"], .msg-s-message-list__message--sent');
  const threadOpen = document.querySelector('.msg-convo-wrapper .msg-s-message-list, .msg-thread--pillar .msg-s-message-list');
  return {
    inConversationList: !!conversationList,
    hasNewMessage: !!newMessage,
    sendButtonGone: !sendButton || sendButton.offsetParent === null,
    threadOpen: !!threadOpen,
    hasError: !!errorMsg,
    errorText: errorMsg ? (errorMsg.textContent || '').trim() : '',
    hasSentConfirmation: !!sentConfirmation
  };
}"""

_CONNECT_BUTTON_JS = """() => {
  const connectBtn = document.querySelector('a[href*="/preload/custom-invite/"], a[href*="/connect/"], button[aria-label="Connect"], button[aria-label*="connect" i]');
  const sendBtn = document.querySelector('button[aria-label="Send invitation"], button[aria-label="Send without a note"], button[aria-label="Send now"], button[aria-label="Done"]');
  const addNoteBtn = document.querySelector('button[aria-label="Add a note"], button[data-control-name="add_note"], button[aria-label*="add a note" i]');
  const noteField = document.querySelector('textarea[name="message"], textarea[aria-label*="note" i], div[role="textbox"][aria-label*="note" i]');
  const errorMsg = document.querySelector('.artdeco-inline-feedback--error, [data-test-error]');
  return {
    hasConnectButton: !!(connectBtn && connectBtn.offsetParent !== null),
    sendButtonEnabled: sendBtn ? !sendBtn.disabled : false,
    hasAddNoteButton: !!(addNoteBtn && addNoteBtn.offsetParent !== null),
    noteFieldPresent: !!noteField,
    hasError: !!errorMsg,
    errorText: errorMsg ? (errorMsg.textContent || '').trim() : ''
  };
}"""


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _extract_profile_id(profile_url):
    """Extract the LinkedIn URN or username from a profile URL."""
    if not profile_url:
        return ""
    parsed = urlparse(profile_url)
    path = parsed.path.strip("/")
    if path.startswith("in/"):
        return path[3:].rstrip("/").split("/")[0]
    return path


def _clean_profile_url(url):
    """Strip tracking query params (e.g. ?miniProfileUrn=...) from /in/ links."""
    if not url:
        return url
    idx = url.find("?")
    return url[:idx] if idx != -1 else url


def _extract_linkedin_urn(page):
    """Extract the member URN from a LinkedIn profile page source."""
    try:
        text = page.evaluate("() => document.body.innerHTML")
        m = re.search(r'"urn:li:person:([a-zA-Z0-9]+)"', text)
        if m:
            return m.group(1)
        m = re.search(r'"profileId":\s*"([a-zA-Z0-9]+)"', text)
        if m:
            return m.group(1)
        m = re.search(r'"memberUrn":"urn:li:person:([a-zA-Z0-9]+)"', text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _extract_company_id(html_text):
    """Extract the numeric LinkedIn company ID from page source. Pure helper."""
    if not html_text:
        return None
    m = re.search(r'"urn:li:company:(\d+)"', html_text)
    if m:
        return m.group(1)
    m = re.search(r'"companyId":\s*(\d+)', html_text)
    if m:
        return m.group(1)
    return None


def _resolve_company_id(ctx, company_slug):
    """Resolve the numeric company ID by visiting the company page once.

    Retries once on transient failures (slow loads / rate limiting).
    """
    if not company_slug:
        return None
    for attempt in range(2):
        page = ctx.new_page()
        try:
            company_url = f"https://www.linkedin.com/company/{company_slug}/"
            if not _navigate_and_wait(page, company_url, timeout=15):
                continue
            return _extract_company_id(page.evaluate("() => document.body.innerHTML"))
        except Exception:
            if attempt == 0:
                time.sleep(2)
        finally:
            try:
                page.close()
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------

def _navigate_and_wait(page, url, timeout=15):
    """Navigate and wait for page to load sufficiently."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        page.wait_for_timeout(2000)
        dl = time.time() + 5
        while time.time() < dl:
            text = (page.evaluate("() => document.body.innerText") or "").strip()
            if len(text) > 50:
                return True
            time.sleep(0.5)
        return True
    except Exception as e:
        print(f"  NAV_ERR: {e}", file=sys.stderr)
        return False


def _check_auth(page):
    """Check if authenticated to LinkedIn. Returns True if signed in."""
    try:
        text = (page.evaluate("() => document.body.innerText") or "")[:500]
        if "Sign in" in text and "Email" in text:
            return False
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Capability check
# ---------------------------------------------------------------------------

def can_message(ctx, profile_url):
    """Check if we can DM a given profile. Returns capability dict.

    Returns:
        {"can_dm": bool, "can_connect": bool, "needs_premium": bool,
         "is_authenticated": bool, "detail": str}
    """
    page = ctx.new_page()
    try:
        if not _navigate_and_wait(page, profile_url):
            return {"can_dm": False, "detail": "page_load_failed"}

        if not _check_auth(page):
            return {"can_dm": False, "is_authenticated": False, "detail": "not_signed_in"}

        cap = page.evaluate(_DM_CAPABILITY_JS)
        return {
            "can_dm": cap.get("hasMessageButton", False) or cap.get("hasComposeBox", False),
            "can_connect": cap.get("hasConnectButton", False),
            "needs_premium": cap.get("hasInmailOption", False),
            "is_authenticated": True,
            "detail": "message_link" if cap.get("hasMessageButton") else (
                "connect_link" if cap.get("hasConnectButton") else (
                    "inmail" if cap.get("hasInmailOption") else "unknown"
                )
            ),
        }
    finally:
        try:
            page.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# DM sending — VERIFIED typeahead flow
# ---------------------------------------------------------------------------

def send_message(ctx, profile_url, message_body, name=None, timeout=30):
    """Send a LinkedIn DM to the given profile.

    Hard sandbox: refuses to transmit while JI_TESTS is set (the pytest
    session sets it via tests/conftest.py), so no test — no matter how
    its mocks resolve — can reach a real send.

    VERIFIED flow (2026-07): the compose URL recipient params do NOT resolve
    the recipient. The working path is the recipient typeahead on
    /messaging/thread/new/:
      1. Type the contact NAME into the typeahead (real keyboard events)
      2. Mouse-click the best matching suggestion
      3. Fill the message box
      4. Click button.msg-form__send-btn (enables after typing)

    Args:
        ctx: Chrome context from chrome_manager.connect()
        profile_url: The person's LinkedIn profile URL
        message_body: Message text
        name: The person's NAME (required — the typeahead searches by name)

    Returns:
        {"status": "sent"|"uncertain"|"failed"|"connect_required"|"premium_required"|"error",
         "conversation_url": str, "detail": str}
    """
    import os
    if os.environ.get("JI_TESTS"):
        return {"status": "sandbox_refused",
                "detail": "JI_TESTS is set — transmission refused (test runner)"}

    if not name:
        return {"status": "error", "detail": "contact name required for typeahead flow"}

    page = ctx.new_page()
    try:
        if not _navigate_and_wait(page, _MESSAGING_URL, timeout=15):
            return {"status": "error", "detail": "messaging_page_load_failed"}

        if not _check_auth(page):
            return {"status": "error", "is_authenticated": False, "detail": "not_signed_in"}

        page.wait_for_timeout(2000)

        # 1. Recipient typeahead
        rec = page.locator('input.msg-connections-typeahead__search-field, input[placeholder*="Type a name"]')
        if rec.count() == 0:
            return {"status": "error", "detail": "recipient_typeahead_not_found"}
        rec.first.click()
        page.keyboard.type(name, delay=50)
        page.wait_for_timeout(3500)

        # 2. Pick the best suggestion (exact name match preferred, else first)
        picked = _pick_suggestion(page, name)
        if not picked:
            return {"status": "error", "detail": "no_matching_suggestion"}

        # 3. Composer appears — detect premium (InMail) vs free
        cap = page.evaluate(_DM_CAPABILITY_JS)
        if cap.get("inmailComposer"):
            # 2nd/3rd-degree InMail composer — premium credits will be used.
            # Report it so the orchestrator can decide, but proceed.
            print(f"  INMAIL_COMPOSER: {name} (2nd/3rd degree — InMail credits)", file=sys.stderr)

        if not cap.get("hasComposeBox"):
            return {"status": "error", "detail": "composer_not_available"}

        # 4. Fill the message box
        filled = _fill_compose_box(page, message_body)
        if not filled:
            return {"status": "failed", "detail": "compose_box_fill_failed"}

        # 5. Click Send
        clicked = _click_send(page)
        if not clicked:
            return {"status": "failed", "detail": "send_button_not_found_or_disabled"}

        page.wait_for_timeout(2000)
        sent = page.evaluate(_MESSAGE_SENT_JS)
        if sent.get("hasSentConfirmation") or sent.get("sendButtonGone") or sent.get("hasNewMessage"):
            return {"status": "sent", "conversation_url": page.url, "detail": "send_confirmed"}
        if sent.get("hasError"):
            return {"status": "failed", "detail": f"send_error: {sent.get('errorText', '')}"}
        if sent.get("threadOpen") and sent.get("inConversationList"):
            return {"status": "sent", "conversation_url": page.url, "detail": "send_confirmed_thread"}
        # Send clicked but unconfirmed — uncertain, not failed (one-shot safety)
        return {"status": "uncertain", "detail": "send_unconfirmed_check_inbox"}

    finally:
        try:
            page.close()
        except Exception:
            pass


def _pick_suggestion(page, name):
    """Mouse-click the best matching typeahead suggestion.

    Real mouse events are required — JS .click() and Enter do NOT register
    with Ember's delegated handlers.
    """
    target = page.evaluate("""(name) => {
        const items = document.querySelectorAll('li.msg-connections-typeahead__search-result');
        const nameLower = (name || '').toLowerCase();
        let best = null;
        for (const li of items) {
            if (li.offsetParent === null) continue;
            const text = (li.textContent || '').toLowerCase();
            // Prefer exact-name suggestion (name boundary match)
            if (text.includes(nameLower)) {
                const r = li.getBoundingClientRect();
                best = {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)};
                break;
            }
        }
        if (!best && items.length) {
            const r = items[0].getBoundingClientRect();
            best = {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)};
        }
        return best;
    }""", name)
    if not target:
        return False
    page.mouse.click(target["x"] + target["w"] // 2, target["y"] + target["h"] // 2)
    page.wait_for_timeout(3000)
    return True


def _fill_compose_box(page, message_body):
    """Fill the message box (free or InMail composer)."""
    box = page.locator('div.msg-form__contenteditable, div[role="textbox"][aria-label*="message" i], div[contenteditable="true"]').first
    try:
        if not box.is_visible(timeout=3000):
            return False
        box.click()
        page.wait_for_timeout(300)
        box.fill(message_body)
        page.wait_for_timeout(500)
        return True
    except Exception:
        try:
            page.evaluate(f"""
                () => {{
                    const boxes = document.querySelectorAll('div.msg-form__contenteditable, div[role="textbox"][aria-label*="message" i], div[contenteditable="true"]');
                    for (const box of boxes) {{
                        if (box.offsetParent !== null) {{
                            box.focus();
                            document.execCommand('insertText', false, {_repr(message_body)});
                            return true;
                        }}
                    }}
                    return false;
                }}
            """)
            return True
        except Exception:
            return False


def _click_send(page):
    """Click the send button. VERIFIED: button.msg-form__send-btn[type="submit"],
    no aria-label, disabled until text entered."""
    for sel in [
        'button.msg-form__send-btn',
        'button[aria-label="Send"]',
        'button.msg-form__send-button',
        'button[type="submit"].msg-form__send-btn',
    ]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000) and btn.is_enabled():
                btn.click()
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Connection requests
# ---------------------------------------------------------------------------

def send_connect_request(ctx, profile_url, note=""):
    """Send a LinkedIn connection request with optional note.

    Hard sandbox: refuses to transmit while JI_TESTS is set (the pytest
    session sets it via tests/conftest.py), so no test — no matter how
    its mocks resolve — can reach a real send.

    VERIFIED (2026-07-30): navigating DIRECTLY to the Connect link's
    /preload/custom-invite/?vanityName={username} URL renders the
    send-invite modal as a page — no click needed. Modal structure:
      - .artdeco-modal.send-invite
      - "Add a note" button:    button[aria-label="Add a note"]
      - Note textarea:          textarea[name="message"]
                                (appears only after Add-a-note click)
      - Send (with note):       button[aria-label="Send invitation"]
                                (disabled until note typed)
      - Send (no note):         button[aria-label="Send without a note"]

    The send click itself is the one unverified step (no live send test
    performed); outcome detection falls back to error-scan + modal-gone.

    Returns:
        {"status": "sent"|"uncertain"|"failed"|"already_connected"|"error", "detail": str}
    """
    import os
    if os.environ.get("JI_TESTS"):
        return {"status": "sandbox_refused",
                "detail": "JI_TESTS is set — transmission refused (test runner)"}

    page = ctx.new_page()
    try:
        username = _extract_profile_id(profile_url)
        if not username:
            return {"status": "error", "detail": "could_not_extract_profile_id"}

        # Direct navigation to the invite preload URL (verified working)
        invite_url = f"https://www.linkedin.com/preload/custom-invite/?vanityName={username}"
        if not _navigate_and_wait(page, invite_url, timeout=15):
            return {"status": "error", "detail": "invite_page_load_failed"}

        if not _check_auth(page):
            return {"status": "error", "is_authenticated": False, "detail": "not_signed_in"}

        modal = page.locator('.artdeco-modal.send-invite')
        if modal.count() == 0:
            # No invite modal — likely already connected or request pending.
            # Check the profile for a Message link as an already-connected signal.
            profile_url2 = f"https://www.linkedin.com/in/{username}/"
            if _navigate_and_wait(page, profile_url2, timeout=15):
                cap = page.evaluate(_DM_CAPABILITY_JS)
                if cap.get("hasMessageButton"):
                    return {"status": "already_connected", "detail": "message_link_present_no_invite_modal"}
            return {"status": "error", "detail": "no_send_invite_modal"}

        if note:
            add_note = page.locator('button[aria-label="Add a note"]')
            if add_note.count() > 0:
                add_note.first.click()
                page.wait_for_timeout(1500)
            textarea = page.locator('textarea[name="message"]')
            if textarea.count() == 0:
                return {"status": "error", "detail": "note_textarea_not_found"}
            textarea.first.fill(note)
            page.wait_for_timeout(500)

        # Send. When a note was requested, "Send without a note" is NOT an
        # acceptable fallback — silently dropping the note sends a
        # different (colder, generic) invitation than the caller asked
        # for, and a connection request cannot be taken back and redone.
        selectors = ['button[aria-label="Send invitation"]',
                     'button[aria-label="Send now"]',
                     'button[aria-label="Done"]']
        if not note:
            selectors.insert(1, 'button[aria-label="Send without a note"]')
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000) and btn.is_enabled():
                    btn.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                continue

        # Outcome detection
        err = page.evaluate("""() => {
            const errEl = document.querySelector('.artdeco-inline-feedback--error, [data-test-error], .send-invite__error');
            return errEl ? (errEl.textContent || '').trim() : '';
        }""")
        if err:
            return {"status": "failed", "detail": f"error: {err}"}
        modal_gone = page.evaluate("""() => {
            const m = document.querySelector('.artdeco-modal.send-invite');
            return !m || m.offsetParent === null;
        }""")
        if modal_gone:
            return {"status": "sent", "detail": "invite_modal_closed_no_error"}
        return {"status": "uncertain", "detail": "send_clicked_unconfirmed"}

    finally:
        try:
            page.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Company people & connections search
# ---------------------------------------------------------------------------

def search_company_connections(ctx, company_slug=None, company_id=None):
    """Search my 1st-degree connections at a company.

    Primary: LinkedIn search URL with network=F + currentCompany filter
    (requires the NUMERIC company ID — a slug won't match).
    Fallback: company people page (returns all employees, not just
    connections) — logged via the returned signal so the failure is
    visible, not silent.

    Returns dict:
        {"connections": [conn_dict], "used": "search"|"fallback", "detail": str}
    """
    page = ctx.new_page()
    try:
        resolved_id = company_id
        if not resolved_id and company_slug:
            resolved_id = _resolve_company_id(ctx, company_slug)

        if resolved_id:
            search_url = (
                f"https://www.linkedin.com/search/results/people/"
                f"?network=%5B%22F%22%5D&currentCompany=%5B{resolved_id}%5D"
            )
            if _navigate_and_wait(page, search_url, timeout=15) and _check_auth(page):
                page.wait_for_timeout(2000)
                connections = _extract_search_results(page)
                if connections:
                    return {"connections": connections, "used": "search",
                            "detail": f"currentCompany={resolved_id}"}

        # Fallback: company people page (surfaces connections first)
        if company_slug:
            company_url = f"https://www.linkedin.com/company/{company_slug}/people/"
            if _navigate_and_wait(page, company_url, timeout=15) and _check_auth(page):
                page.wait_for_timeout(2000)
                connections = _extract_company_people(page)
                if connections:
                    return {"connections": connections, "used": "fallback",
                            "detail": ("search returned 0 1st-degree results — "
                                       "people page (connections-first, mixed degrees)")}

        return {"connections": [], "used": "none", "detail": "no results"}

    finally:
        try:
            page.close()
        except Exception:
            pass


def search_company_employees(ctx, company_slug, team_keywords=None, max_pages=2):
    """Search all employees at a company, optionally filtered by team keywords.

    VERIFIED: the people page is PAGINATED (button[aria-label="Page N"]),
    NOT infinite-scroll. 12 employees per page.

    Args:
        company_slug: LinkedIn company slug (e.g., "mongodbinc")
        team_keywords: List of keywords to filter by (e.g., ["AI", "ML", "machine learning"])
        max_pages: Max pagination pages to visit

    Returns list of employee dicts.
    """
    page = ctx.new_page()
    try:
        company_url = f"https://www.linkedin.com/company/{company_slug}/people/"
        if not _navigate_and_wait(page, company_url, timeout=15):
            return []

        if not _check_auth(page):
            return []

        employees = []
        seen = set()

        for page_num in range(max_pages):
            page.wait_for_timeout(2000)
            batch = _extract_company_people(page)
            for emp in batch:
                key = emp.get("linkedin_url", "") or emp.get("name", "")
                if key and key not in seen:
                    seen.add(key)
                    employees.append(emp)

            # Paginate — the people page uses numbered page buttons
            next_btn = page.locator(f'button[aria-label="Page {page_num + 2}"]')
            try:
                if next_btn.count() == 0 or not next_btn.first.is_visible(timeout=2000):
                    break
                next_btn.first.click()
                page.wait_for_timeout(2500)
            except Exception:
                break

        if team_keywords:
            keywords_lower = [k.lower() for k in team_keywords]
            filtered = []
            for emp in employees:
                role = (emp.get("role", "") + " " + emp.get("headline", "")).lower()
                if any(kw in role for kw in keywords_lower):
                    filtered.append(emp)
            return filtered

        return employees

    finally:
        try:
            page.close()
        except Exception:
            pass


def _extract_search_results(page):
    """Extract people from LinkedIn search results page."""
    js = """() => {
      const results = [];
      const cards = document.querySelectorAll('.reusable-search__result-container, .entity-result');
      for (const card of cards) {
        const link = card.querySelector('a[href*="/in/"]');
        if (!link) continue;
        const name = (link.textContent || '').trim();
        const href = link.href || '';
        const subtitle = card.querySelector('.entity-result__primary-subtitle, .lt-line-clamp__line');
        const role = subtitle ? (subtitle.textContent || '').trim() : '';
        const secondary = card.querySelector('.entity-result__secondary-subtitle');
        const company = secondary ? (secondary.textContent || '').trim() : '';
        const badge = card.querySelector('.entity-result__badge-text, .member-connection-badge');
        const degree = badge ? (badge.textContent || '').trim() : '';
        if (name && href) {
          results.push({
            name: name.replace(/\\s+/g, ' '),
            role: role.replace(/\\s+/g, ' '),
            company: company.replace(/\\s+/g, ' '),
            linkedin_url: href.split('?')[0],
            connection_degree: degree.includes('1st') ? '1st' : degree.includes('2nd') ? '2nd' : '3rd+'
          });
        }
      }
      return results;
    }"""
    try:
        return page.evaluate(js)
    except Exception:
        return []


def _extract_company_people(page):
    """Extract employees from a LinkedIn company people page.

    VERIFIED selectors (2026-07): cards are
    li.org-people-profile-card__profile-card-spacing; the name must come
    from the TITLE link (the image link comes first in DOM and has no text);
    profile URLs carry ?miniProfileUrn= params that must be stripped.
    """
    js = """() => {
      const employees = [];
      const cards = document.querySelectorAll('li.org-people-profile-card__profile-card-spacing');
      for (const card of cards) {
        const titleLink = card.querySelector('.artdeco-entity-lockup__title a[href*="/in/"]');
        const imgLink = card.querySelector('a[href*="/in/"]');
        const link = titleLink || imgLink;
        if (!link) continue;
        const name = (link.textContent || '').trim();
        const href = link.href || '';
        const subtitle = card.querySelector('.artdeco-entity-lockup__subtitle');
        const role = subtitle ? (subtitle.textContent || '').trim() : '';
        const degreeEl = card.querySelector('.artdeco-entity-lockup__degree');
        const degree = degreeEl ? (degreeEl.textContent || '').trim() : '';
        const mutualEl = card.querySelector('span.lt-line-clamp.t-12, span[class*="t-12"]');
        const mutual = mutualEl ? (mutualEl.textContent || '').trim() : '';
        if (name && href) {
          employees.push({
            name: name.replace(/\\s+/g, ' '),
            role: role.replace(/\\s+/g, ' '),
            headline: role.replace(/\\s+/g, ' '),
            linkedin_url: href.split('?')[0],
            connection_degree: degree.replace(/[^0-9]/g, ''),
            notes: mutual.replace(/\\s+/g, ' ')
          });
        }
      }
      return employees;
    }"""
    try:
        return page.evaluate(js)
    except Exception:
        return []


def _repr(s):
    """Safe repr for JS string injection."""
    return json.dumps(s)
