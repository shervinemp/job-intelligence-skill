"""act/fill.py — Hybrid fill command: Playwright-first, Skyvern-fallback."""
import sys, time

from lib.db import get_conn
from apply.common.output import emit_next, emit_status, emit_error, emit_fill_report
from apply.common.page_helpers import load_state, save_state, handle_captcha, handle_session_timeout, tag_page
from apply.act.helpers import (
    _load_profile, chrome_session, _host, _is_error_page, _url_fallbacks,
    _wait_for_fields, _probe_form, _fill_with_playwright, _find_next_button,
    _empty_required, _click_action, _verify_with_ask_api, _detect_submit_button,
    _field_key, _build_ans_dict, _resolve_linkedin_apply, _wire_dialogs,
    _is_junk_field,
)


def cmd_fill(jid, answers: dict = None, verify: bool = True, max_pages: int = 4,
             quick: bool = False):
    db_row = get_conn().execute(
        "SELECT stage, state FROM jobs WHERE id=?", (jid,)
    ).fetchone()
    if not db_row:
        emit_error(f"job {jid} not found")
        return 1
    stage = db_row["stage"]
    if stage == "applied":
        emit_status("already applied")
        emit_next("verify")
        return 0

    state = load_state()
    if state.get("jid") != jid:
        state = {"jid": jid}
    state["jid"] = jid

    url = state.get("external_url") or state.get("url", "")
    if not url:
        row = get_conn().execute("SELECT url, external_url FROM jobs WHERE id=?", (jid,)).fetchone()
        if row:
            url = row["external_url"] or row["url"]
            state["url"] = row["url"]
            state["external_url"] = row["external_url"] or ""
    if not url:
        emit_error("no URL found — run 'apply detect <jid>' first")
        return 1
    orig_url = url

    profile = _load_profile()
    ans_dict = _build_ans_dict(profile, answers)
    if not ans_dict:
        emit_error("no answers resolved — check profile or --answers")
        return 1

    from apply.common.registry import resolve as resolve_registry

    filled_all, failed_all = [], []
    filled_keys = set()
    field_total = 0
    submit_visible = False

    try:
        with chrome_session(state) as (page, ctx):
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            if handle_captcha(page, state):
                emit_status("captcha", "CAPTCHA still present after timeout")
                return 1

            if _host(page.url) and _host(url) and _host(page.url) != _host(url):
                print(f"  REDIRECT: {_host(url)} -> {_host(page.url)}", file=sys.stderr)
                state["external_url"] = page.url
                url = page.url

            fallbacks = _url_fallbacks(url, orig_url)
            if _is_error_page(page):
                for alt in fallbacks:
                    print(f"  Landing page broken — trying fallback: {alt[:90]}", file=sys.stderr)
                    try:
                        page.goto(alt, wait_until="domcontentloaded", timeout=30000)
                        time.sleep(2)
                    except Exception:
                        continue
                    if not _is_error_page(page):
                        state["external_url"] = page.url
                        url = page.url
                        break
                fallbacks = []

            tag_page(page, jid)

            if "linkedin.com/jobs" in (page.url or "").lower():
                resolved = _resolve_linkedin_apply(page)
                if resolved:
                    apply_link = page.locator('a:has-text("Apply")').first
                    clicked = False
                    if apply_link.count() > 0:
                        try:
                            with ctx.expect_page(timeout=15000) as new_page_info:
                                apply_link.click()
                            new_page = new_page_info.value
                            new_page.wait_for_load_state("domcontentloaded", timeout=30000)
                            time.sleep(2)
                            try:
                                new_page.wait_for_selector('iframe', timeout=15000)
                                time.sleep(3)
                            except Exception:
                                pass
                            real_url = new_page.url
                            print(f"  LINKEDIN: Apply -> {real_url[:80]}", file=sys.stderr)
                            get_conn().execute(
                                "UPDATE jobs SET external_url=? WHERE id=?", (real_url, jid)
                            ).connection.commit()
                            state["external_url"] = real_url
                            url = real_url
                            page.close()
                            page = new_page
                            _wire_dialogs(page)
                            tag_page(page, jid)
                            clicked = True
                        except Exception:
                            pass
                    if not clicked:
                        print(f"  LINKEDIN: Apply (direct) -> {resolved[:80]}", file=sys.stderr)
                        get_conn().execute(
                            "UPDATE jobs SET external_url=? WHERE id=?", (resolved, jid)
                        ).connection.commit()
                        state["external_url"] = resolved
                        url = resolved
                        page.goto(resolved, wait_until="domcontentloaded", timeout=30000)
                        time.sleep(2)
                        if _host(page.url) and _host(resolved) and _host(page.url) != _host(resolved):
                            state["external_url"] = page.url
                            url = page.url
                else:
                    ea_btn = page.locator('button:has-text("Easy Apply")').first
                    if ea_btn.count() > 0:
                        ea_btn.click()
                        print("  Easy Apply: modal opened", file=sys.stderr)
                        time.sleep(3)
                    else:
                        print("  WARN: no Apply link and no Easy Apply button — job may be expired", file=sys.stderr)
                        state["status"] = "no_apply_path"
                        save_state(state)
                        emit_status("no_apply_path", "no Easy Apply button or Apply link on LinkedIn page")
                        emit_next("none", "job may be expired — skip or apply via external URL")
                        return 1
                    max_pages = max(max_pages, 6)

            reg = resolve_registry(page.url) or resolve_registry(orig_url)
            if reg and reg.page_range:
                try:
                    max_pages = min(max_pages, int(reg.page_range[-1]))
                except Exception:
                    pass
            from apply.common.page_state import has_form, wait_for_form
            wait_for_form(page, timeout=8)

            if not has_form(page):
                apply_btn = page.locator('a:has-text("Apply"), button:has-text("Apply")').first
                if apply_btn.count() > 0:
                    apply_btn.click()
                    print("  Listing page: clicked Apply", file=sys.stderr)
                    time.sleep(3)
                    wait_for_form(page, timeout=10)

            if not has_form(page):
                for label in ["Apply Manually", "Autofill with Resume"]:
                    btn = page.locator(f'button:has-text("{label}"), a:has-text("{label}")').first
                    if btn.count() > 0:
                        btn.click()
                        print(f"  Apply modal: clicked '{label}'", file=sys.stderr)
                        time.sleep(3)
                        wait_for_form(page, timeout=10)
                        break

            # General no-apply-path check (platform-agnostic).
            # At this point we've tried: LinkedIn Easy Apply/Apply link,
            # generic Apply button, Apply Manually/Autofill modal, and
            # waited 8+10+10s for fields. If no form elements, no dialog,
            # and no iframe with form elements exist, the job is likely
            # expired or the page didn't load a form.
            from apply.common.page_state import has_any_form
            _cwd = reg.widgets if reg and hasattr(reg, 'widgets') else None
            if not has_any_form(page, custom_widget_selectors=_cwd):
                print("  WARN: no form, dialog, or iframe form found — job may be expired", file=sys.stderr)
                state["status"] = "no_apply_path"
                save_state(state)
                emit_status("no_apply_path", "no form or apply path found on page")
                emit_next("none", "job may be expired — skip or apply via external URL")
                return 1

            if not _handle_login_wall(page, jid, quick):
                return 1

            seen = set()
            for page_num in range(1, max_pages + 1):
                pr = _probe_form(page, reg, jid, allow_vision=(page_num == 1))
                if page.url and page.url != url and "about:blank" not in page.url:
                    state["external_url"] = page.url
                    url = page.url
                fields = pr.fields or []
                field_total += len(fields)
                if not fields:
                    print(f"  No fields detected (page {page_num}, strategy={pr.strategy})", file=sys.stderr)
                    if page_num == 1 and fallbacks:
                        alt = fallbacks.pop(0)
                        print(f"  Trying fallback URL: {alt[:90]}", file=sys.stderr)
                        try:
                            page.goto(alt, wait_until="domcontentloaded", timeout=30000)
                            time.sleep(2)
                            _wait_for_fields(page, timeout=8)
                            state["external_url"] = page.url
                            url = page.url
                            reg = resolve_registry(page.url)
                        except Exception:
                            pass
                        continue

                filled, failed = _fill_with_playwright(page, fields, profile, answers)
                for rec in filled:
                    if rec["key"] not in filled_keys:
                        filled_keys.add(rec["key"])
                        filled_all.append(rec["label"])
                for rec in failed:
                    k = _field_key(rec)
                    if k not in filled_keys and k not in {_field_key(r) for r in failed_all}:
                        failed_all.append(rec)

                if fields:
                    print(f"  Page {page_num}: filled {len(filled)}/{len(fields)}"
                          + (f" — failed: {', '.join(r['label'] for r in failed[:5])}" if failed else ""), file=sys.stderr)

                fp = (page.url, tuple(sorted(f.get("label", "") for f in fields)))
                if fp in seen:
                    break
                seen.add(fp)
                nxt = _find_next_button(page)
                if not nxt:
                    submit_visible = bool(_detect_submit_button(page))
                    break
                empt = _empty_required(page)
                if empt:
                    print(f"  {empt} required field(s) still empty — not advancing", file=sys.stderr)
                    break
                print(f"  Multi-page: clicking '{nxt['text']}'", file=sys.stderr)
                if not _click_action(page, nxt["text"]):
                    break
                time.sleep(2)
                has_dialog = page.evaluate("""() => !!document.querySelector('[role="dialog"], dialog')""")
                if has_dialog:
                    for _ in range(10):
                        n = page.evaluate("""() => {
                            const d = document.querySelector('[role="dialog"], dialog');
                            if (!d) return 0;
                            return d.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea').length;
                        }""")
                        if n:
                            break
                        time.sleep(1)
                else:
                    _wait_for_fields(page, timeout=5)
                if handle_captcha(page, state):
                    emit_status("captcha", "CAPTCHA during multi-page navigation")
                    return 1
                handle_session_timeout(page)

            # Conditional-reveal sweep: clicking radio/yesno/select values on
            # SPA forms (Ashby, Workday) can reveal NEW required fields
            # that weren't in the original probe. Re-probe and fill any
            # newly-revealed fields that weren't in the original set.
            # Limited to 2 sweeps to avoid infinite loops on dynamic forms.
            for sweep in range(2):
                time.sleep(1)
                pr2 = _probe_form(page, reg, jid, allow_vision=False)
                new_fields = [
                    f for f in (pr2.fields or [])
                    if not _is_junk_field(f)
                    and _field_key(f) not in filled_keys
                    and _field_key(f) not in {_field_key(r) for r in failed_all}
                ]
                if not new_fields:
                    break
                print(f"  Sweep {sweep+1}: {len(new_fields)} new field(s) revealed", file=sys.stderr)
                filled2, failed2 = _fill_with_playwright(page, new_fields, profile, answers)
                for rec in filled2:
                    if rec["key"] not in filled_keys:
                        filled_keys.add(rec["key"])
                        filled_all.append(rec["label"])
                for rec in failed2:
                    k = _field_key(rec)
                    if k not in filled_keys and k not in {_field_key(r) for r in failed_all}:
                        failed_all.append(rec)
                field_total += len(new_fields)
                if filled2:
                    print(f"  Sweep {sweep+1}: filled {len(filled2)}/{len(new_fields)}", file=sys.stderr)
                if not filled2 and not failed2:
                    break

            remaining_now = [r for r in failed_all if _field_key(r) not in filled_keys]
            if verify and filled_all and not remaining_now and field_total > 0:
                try:
                    verify_result = _verify_with_ask_api(page, ans_dict)
                    if not verify_result.get("ok"):
                        mm = verify_result.get("mismatches", [])
                        if mm:
                            print(f"  Vision flag: {len(mm)} field(s) may need review", file=sys.stderr)
                except Exception as ve:
                    print(f"  Vision verify skipped: {ve}", file=sys.stderr)

    except Exception as e:
        emit_error(f"Playwright fill failed: {e}")
        return 1

    remaining = [r for r in failed_all if _field_key(r) not in filled_keys]
    skyvern_fields = [r for r in remaining if r["_why"] == "fill_failed" or r.get("required")]
    skipped = [r for r in remaining if r["_why"] == "no_answer" and not r.get("required")]

    if remaining:
        emit_fill_report(len(filled_all), remaining, 1, profile)
    if skipped:
        skip_labels = [r["label"] for r in skipped]
        print(f"  SKIPPED (optional, no answer): {', '.join(skip_labels)}", file=sys.stderr)

    skyvern_result = None
    needs_skyvern = (bool(skyvern_fields) or field_total == 0) and not quick
    if needs_skyvern:
        n = len(skyvern_fields) if skyvern_fields else 8
        budget = min(30, 6 + 3 * n)
        print(f"  Handing off {n} field(s) to Skyvern (non-blocking, max_steps={budget})...", file=sys.stderr)
        from apply.common.skyvern_bridge import fill_remaining as _fill_remaining
        try:
            skyvern_result = _fill_remaining(
                url=url,
                answers=ans_dict,
                filled_fields=filled_all + [r["label"] for r in skipped],
                wait=False,
                timeout=30,
                max_steps=budget,
            )
            status = skyvern_result.get("status", "unknown")
            print(f"  Skyvern: {status}", file=sys.stderr)
            if skyvern_result.get("browser_session_id"):
                state["browser_session_id"] = skyvern_result["browser_session_id"]
            if skyvern_result.get("run_id"):
                state["fill_run_id"] = skyvern_result["run_id"]
                state["fill_run_started"] = time.time()
                print(f"  Skyvern run_id: {state['fill_run_id']}", file=sys.stderr)
                print(f"  Check status later via 'apply verify {jid}'", file=sys.stderr)
        except Exception as se:
            print(f"  Skyvern fill failed: {se}", file=sys.stderr)

    state["filled_count"] = len(filled_all)
    state["failed_fields"] = [r["label"] for r in skyvern_fields]
    state["skipped_fields"] = [r["label"] for r in skipped]
    if not (skyvern_result and skyvern_result.get("run_id")):
        state.pop("fill_run_id", None)
        state.pop("fill_run_started", None)
    save_state(state)

    if field_total == 0 and not skyvern_result:
        emit_status("unknown", "no fields found by Playwright or Skyvern")
        return 1

    msg = f"Playwright: {len(filled_all)} fields"
    if skyvern_fields:
        msg += f", to Skyvern: {len(skyvern_fields)}"
    if skipped:
        msg += f", skipped optional: {len(skipped)}"
    if skyvern_result:
        msg += f" + Skyvern: {skyvern_result.get('status', 'unknown')}"
    emit_status("filled", msg)

    if skyvern_result and skyvern_result.get("run_id"):
        emit_next("verify", "poll Skyvern fill progress")
    elif submit_visible or filled_all:
        emit_next("check", "run 'apply act --check' to validate before submit")
    else:
        emit_next("act --inspect", "no fillable fields and no Skyvern run")
    return 0


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


def _handle_login_wall(page, jid, quick):
    """Detect login walls and auto-login or auto-create account.
    Returns True if we should continue to form fill, False to stop."""
    from lib.credentials import (
        get_creds, save_creds, get_account_defaults, _domain_from_url,
    )

    try:
        info = page.evaluate(_LOGIN_JS)
    except Exception:
        return True
    if not info:
        return True

    domain = _domain_from_url(page.url)
    print(f"  LOGIN_WALL: {domain}", file=sys.stderr)

    creds = get_creds(domain)
    if creds:
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
                _fill_signin_form(page, creds["email"], tried_pw)
                time.sleep(5)
                result = _login_check(page)
                if result == "yes":
                    print(f"  LOGIN: OK with password #{idx+1}", file=sys.stderr)
                    if tried_pw != creds["password"]:
                        try:
                            remaining = [p for p in creds["passwords"] if p != tried_pw]
                            save_creds(domain, creds["email"], tried_pw, passwords=remaining)
                            print(f"  LOGIN: promoted this password to primary for {domain}", file=sys.stderr)
                        except Exception:
                            pass
                    return True
                if result == "uncertain":
                    # SPA may be slow — wait longer and re-check once.
                    time.sleep(5)
                    if _login_check(page) in ("yes", "uncertain"):
                        # If still uncertain, assume success (don't risk
                        # trying more passwords and locking the account).
                        print(f"  LOGIN: assuming OK (uncertain after extended wait) with password #{idx+1}", file=sys.stderr)
                        if tried_pw != creds["password"]:
                            try:
                                remaining = [p for p in creds["passwords"] if p != tried_pw]
                                save_creds(domain, creds["email"], tried_pw, passwords=remaining)
                            except Exception:
                                pass
                        return True
                    # Re-check said "no" — fall through to try next
                # result == "no" — try next candidate
                _re_open_signin_form(page)
            print(f"  LOGIN: all {len(creds['passwords'])} password(s) failed", file=sys.stderr)
            emit_status("login_failed", f"all {len(creds['passwords'])} password(s) rejected by {domain}")
            emit_next("login", f"domain={domain} jid={jid} — update creds via 'apply.py creds set {domain} <email>'")
            return False
        except Exception as e:
            print(f"  LOGIN_FAIL: {e}", file=sys.stderr)
            return True

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
        emit_status("login_required", f"create account at {domain}")
        emit_next("login", f"domain={domain} jid={jid}")
        return False

    pw_inputs = page.query_selector_all('input[type="password"]')
    if not pw_inputs:
        print(f"  LOGIN_REQUIRED: no creds for {domain}", file=sys.stderr)
        emit_status("login_required", f"sign in or create account at {domain}")
        emit_next("login", f"domain={domain} jid={jid}")
        return False

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
            create_result = _check_account_created(page)
            if create_result in ("yes", "uncertain"):
                save_creds(domain, defaults["email"], new_pw)
                try:
                    from lib.credentials import add_shared_password
                    add_shared_password(new_pw)
                except Exception:
                    pass
                print(f"  ACCOUNT_CREATED: {defaults['email']} @ {domain} — creds saved (also added to shared pool)", file=sys.stderr)
                return True
            else:
                print(f"  CREATE_FAIL: account creation rejected ({create_result})", file=sys.stderr)
                emit_status("login_required", f"account creation rejected at {domain}")
                emit_next("login", f"domain={domain} jid={jid} — create account manually, then 'apply.py creds set {domain} {defaults.get('email','<email>')}'")
                return False
    except Exception as e:
        print(f"  CREATE_FAIL: {e}", file=sys.stderr)
    emit_status("login_required", f"account creation failed at {domain}")
    emit_next("login", f"domain={domain} jid={jid}")
    return False


def _check_account_created(page):
    """Heuristic: did account creation succeed?

    Returns 'yes', 'no', or 'uncertain' — same protocol as _login_check.
    """
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

            // Failure indicators: validation errors
            const errors = /password.*do not match|passwords.*match|password.*weak|password.*requirement|email.*already|account.*exists|please enter|missing|incomplete|invalid email|check the box|must contain|at least/im;
            if (errors.test(txt)) return 'no';

            // Form still visible — could be loading
            if (createVisible) return 'uncertain';
            return 'uncertain';
        }""")
        return result or 'uncertain'
    except Exception:
        return 'uncertain'


# ─── login helper functions used by _handle_login_wall ───────────────

def _fill_signin_form(page, email, password):
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


def _login_check(page):
    """Three-way heuristic: did the just-submitted sign-in form succeed?

    Returns:
      "yes"      — confidently logged in (password field gone, OR
                   greeting text found, OR sign-in form disappeared)
      "no"       — confidently failed (error text found)
      "uncertain"— form still visible, no error, no greeting — could be
                   slow SPA transition. Caller should wait and re-check.
    """
    try:
        result = page.evaluate("""() => {
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
        return result or 'uncertain'
    except Exception:
        return 'uncertain'


def _re_open_signin_form(page):
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
