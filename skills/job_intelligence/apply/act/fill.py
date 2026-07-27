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
                    max_pages = max(max_pages, 6)

            reg = resolve_registry(page.url) or resolve_registry(orig_url)
            if reg and reg.page_range:
                try:
                    max_pages = min(max_pages, int(reg.page_range[-1]))
                except Exception:
                    pass
            _wait_for_fields(page, timeout=8)

            if not page.query_selector('input, select, textarea'):
                apply_btn = page.locator('a:has-text("Apply"), button:has-text("Apply")').first
                if apply_btn.count() > 0:
                    apply_btn.click()
                    print("  Listing page: clicked Apply", file=sys.stderr)
                    time.sleep(3)
                    _wait_for_fields(page, timeout=10)

            if not page.query_selector('input, select, textarea'):
                for label in ["Apply Manually", "Autofill with Resume"]:
                    btn = page.locator(f'button:has-text("{label}"), a:has-text("{label}")').first
                    if btn.count() > 0:
                        btn.click()
                        print(f"  Apply modal: clicked '{label}'", file=sys.stderr)
                        time.sleep(3)
                        _wait_for_fields(page, timeout=10)
                        break

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
    from lib.credentials import get_creds, save_creds, get_account_defaults, gen_password, _domain_from_url

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
        print(f"  Auto-login: {creds['email']}", file=sys.stderr)
        try:
            email_input = page.locator('input[type="email"], input[name*="email" i], input[name*="user" i]').first
            if email_input.count() > 0:
                email_input.fill(creds["email"])
            pw_input = page.locator('input[type="password"]').first
            if pw_input.count() > 0:
                pw_input.fill(creds["password"])
            submit = page.locator('button[type="submit"], input[type="submit"]').first
            if submit.count() > 0:
                try:
                    submit.click(timeout=5000)
                except Exception:
                    submit.click(force=True, timeout=5000)
                time.sleep(5)
                print("  LOGIN: submitted", file=sys.stderr)
            return True
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

    new_pw = gen_password()
    try:
        email_input = page.locator('input[type="email"], input[name*="email" i]').first
        if email_input.count() > 0:
            email_input.fill(defaults["email"])
        for pwi in pw_inputs:
            pwi.fill(new_pw)
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
            save_creds(domain, defaults["email"], new_pw)
            print(f"  ACCOUNT_CREATED: {defaults['email']} @ {domain} — creds saved", file=sys.stderr)
            return True
    except Exception as e:
        print(f"  CREATE_FAIL: {e}", file=sys.stderr)
    emit_status("login_required", f"account creation failed at {domain}")
    emit_next("login", f"domain={domain} jid={jid}")
    return False
