"""act/auth_flow.py — the auth-wall machine (C1 deepening).

One deep module owning the entire login / account-creation / 2FA / CAPTCHA flow
that previously lived inline in act/fill.py (~566 lines, fill.py:933-1498).
fill.py now imports the same functions by name, so every stderr signal the
orchestrator reads is unchanged — this is a relocation of implementation, never
of the evidence trail (trace contract C-O1).

Interface (the test surface):
  handle_login_wall(page, jid, quick) -> str    "" continue, else a stop status
  login_check(page) -> str                      yes/no/2fa/captcha/uncertain
  check_account_created(page) -> str            yes/no/exists/captcha/uncertain
  fill_signin_form(page, email, password) -> None
  reopen_signin_form(page) -> None
  post_login_gates(page, state, deadline) -> bool   True = blocked (2FA/captcha)

The credential vault, the domain gate, and captcha/2FA detection sit behind
this module's calls — the auth seam no longer leaks into the fill loop.
"""

import sys
import time

from apply.common.domain_gate import is_approved as _domain_approved
from apply.common import terms as _T
from apply.common.output import emit_next, emit_status
from apply.act.helpers import _get_validation_errors


_LOGIN_JS = r"""() => {
  const pw = document.querySelector('input[type="password"]');
  if (!pw) return null;
  const form = pw.closest('form') || pw.parentElement?.parentElement;
  if (!form) return null;
  const text = (form.textContent || '').toLowerCase();
  const signIn = !!form.querySelector('button[type="submit"], input[type="submit"]')
    || text.includes('sign in') || text.includes('log in') || text.includes('login');
  if (!signIn) return null;
  const emailInput = form.querySelector('input[type="email"], input[name*="email" i], input[name*="user" i]');
  const createLink = [...document.querySelectorAll('a, button')]
    .find(el => /create (an )?account|register|new (user|applicant)|sign up/i.test(el.textContent || ''));
  return {
    hasEmail: !!emailInput,
    createText: createLink ? createLink.textContent.trim().substring(0, 40) : null,
    createTag: createLink ? createLink.tagName : null,
  };
}"""


def _try_complete_2fa_from_inbox(page, domain):
    """Read the inbox for the security code the platform just sent and enter it.

    Returns "yes" (2FA completed, logged in), "no" (could not complete), or
    "skip" (no inbox reader / nothing attributable found — caller falls back
    to manual handoff). Fail-closed: only a code extracted from an email
    FROM the auth domain is entered; nothing guessed.
    """
    from lib.inbox import find_security_email
    try:
        found = find_security_email(domain, kind="code")
    except Exception as e:
        print(f"  INBOX_SKIP: {e}", file=sys.stderr)
        return "skip"
    if not found:
        print(f"  INBOX_NO_CODE: no security-code email from {domain} found",
              file=sys.stderr)
        return "skip"
    code = found.get("code")
    print(f"  INBOX_CODE: found code for {domain} "
          f"(from {found.get('from','?')})", file=sys.stderr)
    if not code:
        return "skip"
    # Enter the code into the visible one-time-code input.
    try:
        entered = page.evaluate("""(code) => {
            const inputs = document.querySelectorAll(
                'input[autocomplete*="one-time-code" i],'
                + 'input[inputmode="numeric"][maxlength],'
                + 'input[type="tel"][maxlength]');
            for (const inp of inputs) {
                if (inp.offsetParent === null) continue;
                inp.focus();
                inp.value = code;
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }
            return false;
        }""", code)
        if not entered:
            print(f"  INBOX_CODE_ENTER: no 2FA input visible — leaving for "
                  f"manual completion", file=sys.stderr)
            return "skip"
        time.sleep(2)
        # Submit the code (the button may be a numeric-verify submit).
        try:
            sb = page.locator(
                'button[type="submit"], button:has-text("Verify"), '
                'button:has-text("Confirm"), button:has-text("Submit")'
            ).first
            if sb.count() > 0 and sb.is_visible(timeout=2000):
                sb.click(timeout=4000)
        except Exception:
            pass
        time.sleep(5)
        result = login_check(page)
        if result == "yes":
            print(f"  INBOX_CODE_OK: 2FA completed via inbox code", file=sys.stderr)
            return "yes"
        if result == "2fa":
            print(f"  INBOX_CODE_RETRY: still a 2FA prompt after entering code "
                  f"— leaving for manual completion", file=sys.stderr)
            return "skip"
        if result == "no":
            print(f"  INBOX_CODE_REJECTED: code rejected — leaving for manual "
                  f"completion", file=sys.stderr)
            return "skip"
        return "skip"
    except Exception as e:
        print(f"  INBOX_CODE_SKIP: {e}", file=sys.stderr)
        return "skip"


def _try_verify_account_from_inbox(page, domain):
    """Read the inbox for the account-verification link the platform just sent
    and click it (in a new tab). Returns True when a verification link was
    found and opened; False otherwise.

    The link is only opened when it attributes to the auth domain or a known
    verify host — never an arbitrary email link.
    """
    from lib.inbox import find_security_email
    try:
        found = find_security_email(domain, kind="verify",
                                    query_extra="subject:(verify OR confirm OR activate)")
    except Exception as e:
        print(f"  INBOX_VERIFY_SKIP: {e}", file=sys.stderr)
        return False
    if not found:
        print(f"  INBOX_NO_VERIFY: no verification email from {domain} found",
              file=sys.stderr)
        return False
    link = found.get("link")
    if not link:
        return False
    print(f"  INBOX_VERIFY: opening verification link from "
          f"{found.get('from','?')}", file=sys.stderr)
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)
        print(f"  INBOX_VERIFY_OPENED: verification link opened", file=sys.stderr)
        return True
    except Exception as e:
        print(f"  INBOX_VERIFY_FAIL: {e}", file=sys.stderr)
        return False


def handle_login_wall(page, jid, quick):
    """Detect login walls and auto-login or auto-create account.

    Returns a status string: "" to continue to form fill, or one of
    _T.STATUS_LOGIN_REQUIRED / _T.STATUS_LOGIN_FAILED / _T.STATUS_2FA_REQUIRED
    to stop. The caller persists it into state so the orchestrator can classify
    the fill failure instead of treating it as a generic exception.
    """
    from lib.credentials import (
        get_creds, save_creds, get_account_defaults, _domain_from_url,
    )

    try:
        info = page.evaluate(_LOGIN_JS)
    except Exception:
        return ""
    if not info:
        return ""

    domain = _domain_from_url(page.url)
    print(f"  LOGIN_WALL: {domain}", file=sys.stderr)

    # Try guest apply first — some platforms (Workday, etc.) offer
    # "Continue without signing in" / "Apply as guest". Check registry
    # patterns for the current platform.
    from apply.common.registry import resolve as resolve_registry
    reg = resolve_registry(page.url)
    if reg:
        for pattern in reg.patterns.get("guest_apply", []):
            try:
                btn = page.locator(f'button:has-text("{pattern}"), a:has-text("{pattern}")').first
                if btn.count() > 0 and btn.is_visible(timeout=2000):
                    btn.click(timeout=5000)
                    time.sleep(2)
                    print(f"  GUEST_APPLY: clicked '{pattern}'", file=sys.stderr)
                    return ""
            except Exception:
                continue

    creds = get_creds(domain)
    if creds:
        # ADVERSARIAL #2-A: never type a saved password into a login form on a
        # domain we have NOT successfully authenticated before. A persuasive
        # fake ATS (public host, real-looking sign-in form) would otherwise
        # receive the user's real credentials. Reuses the F2 domain-approval
        # gate: a domain must be approved (or have prior auth) before its
        # password is typed.
        try:
            if not _domain_approved(domain):
                print(f"  CRED_GUARD: '{domain}' has no prior successful auth — "
                      f"refusing to type saved credentials. Approve it: "
                      f"report.py domains approve {domain}", file=sys.stderr)
                emit_status(_T.STATUS_LOGIN_REQUIRED,
                            f"domain={domain} needs approval before credentials "
                            "are used")
                return _T.STATUS_LOGIN_REQUIRED
        except Exception:
            pass
        print(f"  Auto-login: {creds['email']} ({len(creds['passwords'])} password(s))", file=sys.stderr)
        try:
            # Accept cookie banners that can intercept clicks (Workday, etc.)
            for sel in [
                '[data-automation-id="legalNoticeAcceptButton"]',
                'button:has-text("Accept Cookies")',
                'button:has-text("Accept")',
            ]:
                try:
                    bn = page.locator(sel).first
                    if bn.count() > 0 and bn.is_visible():
                        bn.click(timeout=1500)
                        time.sleep(1)
                        print("  Cookies accepted", file=sys.stderr)
                        break
                except Exception:
                    continue

            # Workday defaults to the "Create Account" form with a "Sign
            # In" link. Click it first so the sign-in form renders (the
            # Create Account form has 2 password fields + a checkbox —
            # filling that with saved creds always fails validation).
            for sel in [
                '[data-automation-id="signInLink"]',
                'a:has-text("Sign In")',
                'button:has-text("Sign In")',
            ]:
                try:
                    link = page.locator(sel).first
                    if link.count() > 0 and link.is_visible():
                        link.click(timeout=3000)
                        time.sleep(2)
                        print("  Switched to Sign In form", file=sys.stderr)
                        break
                except Exception:
                    continue

            # Try each password candidate until one succeeds.
            # Strategy: if the first attempt is "uncertain" (SPA slow to
            # transition), wait longer and re-check ONCE before concluding
            # failure and trying the next candidate. This avoids double-
            # submitting with wrong passwords on slow platforms.
            for idx, tried_pw in enumerate(creds["passwords"]):
                fill_signin_form(page, creds["email"], tried_pw)
                time.sleep(5)
                result = login_check(page)
                if result == "yes":
                    print(f"  LOGIN: OK with password #{idx+1}", file=sys.stderr)
                    if tried_pw != creds["password"]:
                        try:
                            remaining = [p for p in creds["passwords"] if p != tried_pw]
                            save_creds(domain, creds["email"], tried_pw, passwords=remaining)
                            print(f"  LOGIN: promoted this password to primary for {domain}", file=sys.stderr)
                        except Exception:
                            pass
                    return ""
                if result == "2fa":
                    # Login credentials accepted — platform wants a 2FA
                    # code now. Don't try more passwords (they're all
                    # the same account) — they'd just re-trigger 2FA.
                    # Save the verified password and surface for the
                    # user to complete 2FA manually.
                    print(f"  LOGIN: 2FA required after password #{idx+1} (credentials accepted)",
                          file=sys.stderr)
                    if tried_pw != creds["password"]:
                        try:
                            remaining = [p for p in creds["passwords"] if p != tried_pw]
                            save_creds(domain, creds["email"], tried_pw, passwords=remaining)
                        except Exception:
                            pass
                    # Try inbox completion first — a security code emailed
                    # by the platform can be read and entered automatically.
                    inbox = _try_complete_2fa_from_inbox(page, domain)
                    if inbox == "yes":
                        return ""
                    if inbox == "skip":
                        emit_status(_T.STATUS_2FA_REQUIRED,
                                    f"domain={domain} credentials accepted — "
                                    "complete 2FA manually then rerun")
                        emit_next("login",
                                  f"domain={domain} jid={jid} — complete 2FA in Chrome then rerun fill")
                        return _T.STATUS_2FA_REQUIRED
                if result == "captcha":
                    # CAPTCHA blocking the auth form — must never be treated
                    # as an uncertain-but-OK login. Record captcha_required
                    # and hand off for a human solve.
                    print(f"  LOGIN: CAPTCHA blocking auth at {domain} — "
                          f"recording captcha_required", file=sys.stderr)
                    emit_status(_T.STATUS_CAPTCHA_REQUIRED,
                                f"domain={domain} login blocked by CAPTCHA")
                    emit_next("login",
                              f"domain={domain} jid={jid} — solve CAPTCHA in "
                              "Chrome then rerun fill")
                    return _T.STATUS_CAPTCHA_REQUIRED
                if result == "uncertain":
                    # SPA may be slow — wait longer and re-check once.
                    time.sleep(5)
                    again = login_check(page)
                    if again == "captcha":
                        print(f"  LOGIN: CAPTCHA blocking auth at {domain} — "
                              f"recording captcha_required", file=sys.stderr)
                        emit_status(_T.STATUS_CAPTCHA_REQUIRED,
                                    f"domain={domain} login blocked by CAPTCHA")
                        emit_next("login",
                                  f"domain={domain} jid={jid} — solve CAPTCHA "
                                  "in Chrome then rerun fill")
                        return _T.STATUS_CAPTCHA_REQUIRED
                    if again in ("yes", "uncertain"):
                        # If still uncertain, assume success (don't risk
                        # trying more passwords and locking the account).
                        print(f"  LOGIN: assuming OK (uncertain after extended wait) with password #{idx+1}", file=sys.stderr)
                        if tried_pw != creds["password"]:
                            try:
                                remaining = [p for p in creds["passwords"] if p != tried_pw]
                                save_creds(domain, creds["email"], tried_pw, passwords=remaining)
                            except Exception:
                                pass
                        return ""
                    # Re-check said "no" — fall through to try next
                # result == "no" — try next candidate
                reopen_signin_form(page)
            print(f"  LOGIN: all {len(creds['passwords'])} password(s) failed", file=sys.stderr)
            emit_status(_T.STATUS_LOGIN_FAILED, f"all {len(creds['passwords'])} password(s) rejected by {domain}")
            emit_next("login", f"domain={domain} jid={jid} — update creds via 'apply.py creds set {domain} <email>'")
            return _T.STATUS_LOGIN_FAILED
        except Exception as e:
            print(f"  LOGIN_FAIL: {e}", file=sys.stderr)
            return ""

    if info.get("createText"):
        print(f"  CREATE_ACCOUNT: clicking '{info['createText']}'", file=sys.stderr)
        try:
            btn = page.locator(
                f'{info["createTag"].lower()}:has-text("{info["createText"]}")'
            ).first
            if btn.count() > 0:
                btn.click(force=True, timeout=5000)
                time.sleep(3)
        except Exception:
            pass

    defaults = get_account_defaults()
    if not defaults.get("email"):
        print(f"  LOGIN_REQUIRED: no creds for {domain}, no profile email", file=sys.stderr)
        emit_status(_T.STATUS_LOGIN_REQUIRED, f"create account at {domain}")
        emit_next("login", f"domain={domain} jid={jid}")
        return _T.STATUS_LOGIN_REQUIRED

    pw_inputs = page.query_selector_all('input[type="password"]')
    if not pw_inputs:
        print(f"  LOGIN_REQUIRED: no creds for {domain}", file=sys.stderr)
        emit_status(_T.STATUS_LOGIN_REQUIRED, f"sign in or create account at {domain}")
        emit_next("login", f"domain={domain} jid={jid}")
        return _T.STATUS_LOGIN_REQUIRED

    # Pick a password that satisfies platform complexity rules, preferring
    # the user's shared password pool entries when applicable so account
    # creation stays consistent with manual accounts the user already has.
    # If none fit, the local LLM (ask_api) generates a new password in
    # the same style, satisfying the platform's rules. The new password
    # is saved to the shared pool on successful account creation below.
    from lib.credentials import (
        get_shared_passwords, pick_password_for_platform, gen_password_for_platform,
    )
    shared_pws = get_shared_passwords()
    new_pw = pick_password_for_platform(page.url, shared_pws, page=page)
    if not new_pw:
        # No existing password fits the platform's rules.
        # Local LLM (if available) will read page text + rules and
        # generate a password in the same style as the user's previous
        # passwords. Falls back to a secure random generator if LLM
        # unavailable or returns an unusable password.
        new_pw = gen_password_for_platform(page.url, page=page, existing_pws=shared_pws)
        print(f"  GEN_PASSWORD: generated new password (len={len(new_pw)}) for {domain}", file=sys.stderr)
    try:
        email_input = page.locator('input[type="email"], input[name*="email" i]').first
        if email_input.count() > 0:
            email_input.fill(defaults["email"])
        for pwi in pw_inputs:
            pwi.fill(new_pw)
        # Workday Create Account form has a mandatory checkbox
        # (data-automation-id="createAccountCheckbox") — "I understand..."
        try:
            cb = page.locator('[data-automation-id="createAccountCheckbox"]').first
            if cb.count() > 0 and not cb.is_checked():
                cb.click(timeout=2000)
                print("  Checked create-account acknowledgement", file=sys.stderr)
        except Exception:
            pass
        first_input = page.locator('input[name*="first" i], input[name*="given" i]').first
        if first_input.count() > 0:
            first_input.fill(defaults.get("first_name", ""))
        last_input = page.locator('input[name*="last" i], input[name*="family" i]').first
        if last_input.count() > 0:
            last_input.fill(defaults.get("last_name", ""))
        submit = page.locator('button[type="submit"], input[type="submit"], [data-automation-id="createAccountSubmitButton"]').first
        if submit.count() > 0:
            try:
                submit.click(timeout=5000)
            except Exception:
                submit.click(force=True, timeout=5000)
            time.sleep(5)
            # Verify account creation succeeded before saving creds.
            # Heuristic: Create Account form is gone (no createAccountSubmitButton
            # visible) AND no error text about password/email mismatch.
            create_result = check_account_created(page)
            if create_result == "captcha":
                # Account-creation blocked by a CAPTCHA — do NOT save creds
                # for an account that was never created (the old code folded
                # this into 'uncertain' and wrote the password to disk).
                print(f"  CREATE_CAPTCHA: account creation blocked by CAPTCHA "
                      f"at {domain} — recording captcha_required",
                      file=sys.stderr)
                emit_status(_T.STATUS_CAPTCHA_REQUIRED,
                            f"domain={domain} account creation blocked by CAPTCHA")
                emit_next("login",
                          f"domain={domain} jid={jid} — solve CAPTCHA in "
                          "Chrome then rerun fill")
                return _T.STATUS_CAPTCHA_REQUIRED
            if create_result in ("yes", "uncertain"):
                save_creds(domain, defaults["email"], new_pw)
                try:
                    from lib.credentials import add_shared_password
                    add_shared_password(new_pw)
                except Exception:
                    pass
                print(f"  ACCOUNT_CREATED: {defaults['email']} @ {domain} — creds saved (also added to shared pool)", file=sys.stderr)
                # Some platforms send a "verify your email" link after
                # account creation. Try to complete it via the inbox so the
                # account is fully usable — never blocks, best-effort.
                _try_verify_account_from_inbox(page, domain)
                return ""
            if create_result == "exists":
                # Email already registered — try signing in with the
                # generated password and any shared candidates before
                # handing off to the manual step.
                print(f"  ACCOUNT_EXISTS: {defaults['email']} @ {domain} — trying sign-in with known passwords", file=sys.stderr)
                reopen_signin_form(page)
                signin_tried = [new_pw] + list(get_shared_passwords())
                for pw in signin_tried:
                    fill_signin_form(page, defaults["email"], pw)
                    time.sleep(4)
                    r = login_check(page)
                    if r == "captcha":
                        print(f"  LOGIN: CAPTCHA blocking auth at {domain} — "
                              f"recording captcha_required", file=sys.stderr)
                        emit_status(_T.STATUS_CAPTCHA_REQUIRED,
                                    f"domain={domain} login blocked by CAPTCHA")
                        emit_next("login",
                                  f"domain={domain} jid={jid} — solve CAPTCHA "
                                  "in Chrome then rerun fill")
                        return _T.STATUS_CAPTCHA_REQUIRED
                    if r == "yes":
                        save_creds(domain, defaults["email"], pw)
                        print(f"  LOGIN: OK on existing account (password matched known pool)", file=sys.stderr)
                        return ""
                    if r == "2fa":
                        save_creds(domain, defaults["email"], pw)
                        emit_status(_T.STATUS_2FA_REQUIRED,
                                    f"domain={domain} credentials accepted — "
                                    "complete 2FA manually then rerun")
                        emit_next("login", f"domain={domain} jid={jid}")
                        return _T.STATUS_2FA_REQUIRED
                    reopen_signin_form(page)
                print(f"  ACCOUNT_EXISTS: no known password works — create/reset manually", file=sys.stderr)
            print(f"  CREATE_FAIL: account creation rejected ({create_result})", file=sys.stderr)
            try:
                for _e in _get_validation_errors(page)[:6]:
                    print(f"    ! {_e[:110]}", file=sys.stderr)
            except Exception:
                pass
            emit_status(_T.STATUS_LOGIN_REQUIRED, f"account creation rejected at {domain}")
            emit_next("login", f"domain={domain} jid={jid} — create account manually, then 'apply.py creds set {domain} {defaults.get('email','<email>')}'")
            return _T.STATUS_LOGIN_REQUIRED
    except Exception as e:
        print(f"  CREATE_FAIL: {e}", file=sys.stderr)
    emit_status(_T.STATUS_LOGIN_REQUIRED, f"account creation failed at {domain}")
    emit_next("login", f"domain={domain} jid={jid}")
    return _T.STATUS_LOGIN_REQUIRED


def check_account_created(page):
    """Heuristic: did account creation succeed?

    Returns 'yes', 'no', 'exists', 'captcha', or 'uncertain' — same protocol
    as login_check. 'exists' means the email is already registered, so the
    caller should attempt sign-in instead of create. 'captcha' means a CAPTCHA
    is blocking the create form — the caller must NOT treat it as 'uncertain'
    (which saves creds for an account that was never created).
    """
    from apply.common.page_helpers import check_captcha
    try:
        result = page.evaluate("""() => {
            const txt = (document.body.innerText || '').toLowerCase();
            const createBtn = document.querySelector('[data-automation-id="createAccountSubmitButton"]');
            const createVisible = createBtn && createBtn.offsetParent !== null;
            const pws = Array.from(document.querySelectorAll('input[type="password"]'))
                .filter(p => p.offsetParent !== null);

            // Success indicators: form gone, greeting, next-step content
            const greetings = /welcome|signed in|my account|log out|sign out|continue|step 2|personal information|my information|next step/im;
            if (greetings.test(txt) && !createVisible) return 'yes';
            if (!createVisible && pws.length === 0) return 'yes';

            // Account already registered — checked BEFORE generic errors
            // (the generic list contains "email.*already" too).
            if (/already (have|has|registered)|already exists|account.*already exists|email.*already|already in use|already used/i.test(txt)) return 'exists';

            // Failure indicators: validation errors
            const errors = /password.*do not match|passwords.*match|password.*weak|password.*requirement|account.*exists|please enter|missing|incomplete|invalid email|check the box|must contain|at least/im;
            if (errors.test(txt)) return 'no';

            // Form still visible — could be loading
            if (createVisible) return 'uncertain';
            return 'uncertain';
        }""")
        result = result or 'uncertain'
        # CAPTCHA blocking the create form must surface as its own signal —
        # the caller's uncertain path saves creds for a created account.
        if result == "uncertain" and check_captcha(page):
            return "captcha"
        return result
    except Exception:
        return 'uncertain'


# ─── login helper functions used by handle_login_wall ───────────────

def fill_signin_form(page, email, password):
    """Fill a sign-in form atomically via page.evaluate.

    Targets the visible sign-in form (1 password field, prefers
    data-automation-id=signInSubmitButton). Falls back to generic
    locators if no atomic form is found.
    """
    result = page.evaluate("""(creds) => {
        const forms = Array.from(document.querySelectorAll('form'));
        for (const f of forms) {
            if (f.offsetParent === null) continue;
            const pws = f.querySelectorAll('input[type="password"]');
            if (pws.length !== 1) continue;
            const btn = f.querySelector('[data-automation-id="signInSubmitButton"]');
            if (!btn) continue;
            const emailEl = f.querySelector('input[type="email"], input[type="text"]');
            if (emailEl) {
                emailEl.focus();
                emailEl.value = creds.email;
                emailEl.dispatchEvent(new Event('input', {bubbles: true}));
                emailEl.dispatchEvent(new Event('change', {bubbles: true}));
            }
            pws[0].focus();
            pws[0].value = creds.password;
            pws[0].dispatchEvent(new Event('input', {bubbles: true}));
            pws[0].dispatchEvent(new Event('change', {bubbles: true}));
            btn.click();
            return true;
        }
        return false;
    }""", {"email": email, "password": password})
    if result:
        return
    # Generic fallback
    try:
        ei = page.locator('input[type="email"], input[name*="email" i], input[name*="user" i]').first
        if ei.count() > 0:
            ei.fill(email)
        pi = page.locator('input[type="password"]').first
        if pi.count() > 0:
            pi.fill(password)
        sb = page.locator(
            '[data-automation-id="signInSubmitButton"], '
            'button:has-text("Sign In"), '
            'button[type="submit"], input[type="submit"]'
        ).first
        if sb.count() > 0:
            try:
                sb.click(timeout=5000)
            except Exception:
                sb.click(force=True, timeout=5000)
    except Exception:
        pass


def login_check(page):
    """Three-way heuristic: did the just-submitted sign-in form succeed?

    Returns:
      "yes"      — confidently logged in (password field gone, OR
                   greeting text found, OR sign-in form disappeared)
      "no"       — confidently failed (error text found)
      "2fa"      — login succeeded but 2FA code is now required.
                   Caller must NOT treat this as success — leave the
                   user at the 2FA prompt so they can complete it.
      "captcha"  — a CAPTCHA is blocking the auth form. Caller must NOT
                   treat the unresolved form as "uncertain" and assume
                   OK (that would record a login that never happened).
      "uncertain"— form still visible, no error, no greeting — could be
                   slow SPA transition. Caller should wait and re-check.
    """
    from apply.common.page_helpers import check_captcha
    try:
        result = page.evaluate(r"""() => {
            const txt = (document.body.innerText || '').toLowerCase();
            const visiblePws = Array.from(document.querySelectorAll('input[type="password"]'))
                .filter(p => p.offsetParent !== null);
            const signInBtn = document.querySelector('[data-automation-id="signInSubmitButton"]');
            const signInVisible = signInBtn && signInBtn.offsetParent !== null;
            const createAcctBtn = document.querySelector('[data-automation-id="createAccountSubmitButton"]');
            const createAcctVisible = createAcctBtn && createAcctBtn.offsetParent !== null;

            // Greetings indicate success regardless of form state.
            const greetings = /signed in as|welcome back|my account|log out|sign out|hi,\\s|hello,\\s/im;
            if (greetings.test(txt)) return 'yes';

            // 2FA interstitial — detect BEFORE error checks, since
            // an accompanying "verification required" message can
            // contain 'incorrect'-like phrases that would misroute
            // to 'no'.
            // Triggers: numeric input with maxlength 4-8, OR
            // autocomplete='one-time-code', OR explicit body phrase.
            const twoFAinputs = document.querySelectorAll(
                'input[autocomplete*="one-time-code" i],'
                + 'input[inputmode="numeric"][maxlength],'
                + 'input[type="tel"][maxlength]'
            );
            for (const inp of twoFAinputs) {
                if (inp.offsetParent === null) continue;
                const ml = parseInt(inp.getAttribute('maxlength') || '0', 10);
                const ac = inp.getAttribute('autocomplete') || '';
                if ((ml >= 4 && ml <= 8) || /one-time-code/i.test(ac)) {
                    return '2fa';
                }
            }
            if (/\\b\d{1,2}-?digit (code|verification|otp)\\b/.test(txt)
                || /two-?factor|2fa|verification code|authentication code|enter the code/i.test(txt)) {
                return '2fa';
            }

            // Error text indicates failure.
            const errors = /invalid email|incorrect password|password incorr|account does not exist|no account found|account is locked|failed login|wrong password|email or password is incorrect/im;
            if (errors.test(txt)) return 'no';

            // No password fields visible — form likely closed → success.
            if (visiblePws.length === 0) return 'yes';

            // Sign-in button gone but password still visible — ambiguous.
            if (!signInVisible && !createAcctVisible) return 'uncertain';

            // Sign-in form still visible — likely failed, but could be slow.
            return 'uncertain';
        }""")
        result = result or 'uncertain'
        # A CAPTCHA on the auth form must never read as "uncertain" — the
        # caller's uncertain path assumes OK and would record a login that
        # never happened. Surface it explicitly so the caller records
        # captcha_required instead.
        if result == "uncertain" and check_captcha(page):
            return "captcha"
        return result
    except Exception:
        return 'uncertain'


def reopen_signin_form(page):
    """If a wrong-password attempt left us on a non-sign-in tab (e.g. back to
    Create Account), re-click the Sign In link so the next candidate can be
    tried without manual interaction.
    """
    try:
        for sel in [
            '[data-automation-id="signInLink"]',
            'a:has-text("Sign In")',
            'button:has-text("Sign In")',
        ]:
            try:
                link = page.locator(sel).first
                if link.count() > 0 and link.is_visible():
                    link.click(timeout=1500)
                    time.sleep(1)
                    return
            except Exception:
                continue
    except Exception:
        pass


def post_login_gates(page, state, deadline=None):
    """Post-login 2FA + CAPTCHA gates (fill loop integration point).

    Some platforms show the 6-digit code prompt or a CAPTCHA only AFTER
    sign-in succeeds — the mid-login heuristics can't see them. Runs the
    capability scan (2FA) then handle_captcha, emitting and persisting the
    stop status if either blocks. Returns True if the flow must stop (the
    caller returns immediately), False to continue filling.

    This is the grilling item A/B boundary: two sequential gates, not one
    verdict — a page can show 2FA AND a captcha, and each is handled at its
    own step.
    """
    import time as _t
    from apply.common.capabilities import scan as _cap_scan
    from apply.common.page_helpers import handle_captcha, save_state

    # Post-login 2FA gate: capability scan catches the 6-digit interstitial.
    try:
        _login_profile = _cap_scan(page)
        if _login_profile and _login_profile.get("two_factor_signals"):
            emit_status(_T.STATUS_2FA_REQUIRED,
                        "2FA interstitial after login — complete in Chrome then rerun")
            emit_next("login", "complete 2FA then rerun fill")
            state["status"] = _T.STATUS_2FA_REQUIRED
            save_state(state)
            return True
    except Exception:
        pass

    # Post-login CAPTCHA gate: a reCAPTCHA/hCaptcha can appear only after
    # sign-in (or after the guest-apply click).
    try:
        if handle_captcha(page, state,
                          wait_s=None if not deadline else max(0, int(deadline - _t.time()))):
            emit_status(_T.STATUS_CAPTCHA_REQUIRED,
                        "CAPTCHA after login transition — solve then rerun")
            state["status"] = _T.STATUS_CAPTCHA_REQUIRED
            save_state(state)
            return True
    except Exception:
        pass
    return False
