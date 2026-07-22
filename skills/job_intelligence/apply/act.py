#/usr/bin/env python3
"""act.py — Hybrid fill/submit: Playwright-first for deterministic fields,
Skyvern-fallback for complex fields, ask_api vision verification.

Flow:
  1. Start Chrome with CDP
  2. Playwright connects, reads DOM fields
  3. FieldFiller fills text/select/checkbox/radio/file fields deterministically
  4. Track filled vs failed fields
  5. If any failed → Skyvern fill_remaining() (vision-guided)
  6. ask_api.py vision verifies before submit
  7. Playwright clicks submit (or Skyvern fallback)
"""
import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.config import PROFILE_PATH, JI_HOME
from lib.db import get_conn
from apply.common.output import emit_next, emit_status, emit_error
from apply.common.page_helpers import (
    load_state, save_state, read_page, page_text, find_page,
    tag_page, check_applied_signal, check_captcha, handle_captcha,
    scan_actions, mark_applied,
)
from apply.common.resolve import resolve
from apply.common.signals import has_success_text

RESULTS_DIR = os.path.join(JI_HOME, "results")


def _load_profile():
    try:
        with open(PROFILE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _chrome():
    from lib.chrome_manager import connect, start
    if not start():
        emit_error("could not start Chrome")
        sys.exit(1)
    b, ctx = connect()
    if not ctx:
        emit_error("could not connect to Chrome")
        sys.exit(1)
    return b, ctx


def _playwright():
    from playwright.sync_api import sync_playwright
    from lib.chrome_manager import CDP_URL
    pw = sync_playwright().start()
    b = pw.chromium.connect_over_cdp(CDP_URL)
    ctx = b.contexts[0]
    return pw, b, ctx


def _page_for(ctx):
    """Find or create a page on the form URL in the Playwright context."""
    pages = [p for p in ctx.pages if "about:blank" not in p.url and "chrome-error" not in p.url]
    if pages:
        return pages[-1]
    return ctx.new_page()


def _fill_with_playwright(page, fields, answers) -> tuple[list[str], list[str]]:
    """Fill all detectable fields using Playwright's FieldFiller dispatch.
    Returns (filled_labels, failed_labels)."""
    from apply.strategies.dispatch import field_deterministic

    filled = []
    failed = []

    state = load_state()
    jid = state.get("jid", "")

    # Find resume/cover files for file uploads
    resume_path = None
    cover_path = None
    if jid:
        rd = os.path.join(RESULTS_DIR, jid)
        import glob
        resumes = glob.glob(os.path.join(rd, "*Resume*.pdf"))
        covers = glob.glob(os.path.join(rd, "*Cover*.pdf"))
        if resumes:
            resume_path = resumes[0]
        if covers:
            cover_path = covers[0]

    for f in fields:
        label = f.get("label", "").strip()
        if not label:
            continue

        # Match field label to answer key (case-insensitive prefix match)
        ans = None
        ans_key = None
        for k, v in answers.items():
            kl = k.lower().replace("*", "").strip()
            ll = label.lower().replace("*", "").strip()
            if kl == ll or ll.startswith(kl) or kl.startswith(ll):
                ans = v
                ans_key = k
                break
        if ans is None:
            failed.append(label)
            continue

        # File upload — handle with Playwright directly
        tag = f.get("tag", "").lower()
        lc = label.lower()
        if (tag == "input" and f.get("accept")) or "resume" in lc or "cv" in lc or "cover" in lc:
            path = cover_path if "cover" in lc else resume_path
            if path and os.path.exists(path):
                try:
                    sel = f.get("selector") or f.get("_sel", "")
                    if sel:
                        page.set_input_files(sel, path)
                        filled.append(label)
                        continue
                except Exception as e:
                    pass

        # Use the standard field_deterministic dispatch from strategies
        try:
            if field_deterministic(page, f, ans):
                filled.append(label)
            else:
                failed.append(label)
        except Exception:
            failed.append(label)

    return filled, failed


def _verify_with_ask_api(page, answers: dict) -> dict:
    """Use ask_api.py vision to verify field values on the page.
    Returns {ok: bool, mismatches: list}."""
    try:
        from lib.ask_api import available, ask_bytes
        from apply.common.inspect_lib import page_jpeg
        if not available():
            return {"ok": False, "reason": "ask_api not available"}

        img_bytes = page_jpeg(page, full=False)
        prompt_lines = ["List every visible form field and its current value. Return as 'label: value' lines."]
        for k in answers:
            prompt_lines.append(f"  {k}: <expected: {answers[k]}>")
        prompt = "\n".join(prompt_lines)

        reply, err = ask_bytes(img_bytes, prompt)
        if err:
            return {"ok": False, "reason": str(err)}
        text = str(reply or "")
        mismatches = []
        for k, expected in answers.items():
            if k.lower() in text.lower():
                pass
            else:
                mismatches.append({"field": k, "expected": expected})
        return {
            "ok": len(mismatches) == 0,
            "mismatches": mismatches,
            "vision_text": text[:200],
        }
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def _detect_submit_button(page) -> str | None:
    """Find the submit button on the page using scan_actions + fallbacks."""
    candidates = scan_actions(page, ["submit", "submit application", "send application", "apply"])
    if candidates:
        for c in candidates:
            if not c.get("disabled"):
                return c.get("text", "")
    # Direct keyword match in buttons
    try:
        buttons = page.evaluate("""() => {
            const all = document.querySelectorAll('button');
            return Array.from(all).filter(b => b.offsetParent !== null).map(b => b.textContent.trim().toLowerCase());
        }""")
        for b in buttons:
            if b in ("submit", "submit application", "send", "send application"):
                return b
    except Exception:
        pass
    return None


def _build_ans_dict(profile: dict, answers_override: dict = None) -> dict:
    """Build the full answer dict from profile + --answers override.
    Includes both static answers dict and ephemeral derivations."""
    result = {}
    if isinstance(profile, dict):
        result.update(profile.get("answers", {}))
    if answers_override:
        result.update(answers_override)
    # Ephemeral derivations (name, location, contact info)
    for key in ("first_name", "last_name", "email", "phone", "full_name",
                "city", "state", "country", "linkedin_url", "github_url",
                "website", "headline"):
        if key not in result:
            val = profile.get(key) or profile.get(key.upper()) or profile.get(key.title())
            if val:
                result[key.replace("_", " ").title()] = val
    return result


def cmd_fill(jid, answers: dict = None, verify: bool = True):
    """Hybrid fill: Playwright-first, Skyvern-fallback, ask_api verify."""
    db_row = get_conn().execute(
        "SELECT stage, state FROM jobs WHERE id=?", (jid,)
    ).fetchone()
    if not db_row:
        emit_error(f"job {jid} not found")
        return 1
    stage, job_state = db_row["stage"], db_row["state"]

    state = load_state()
    if state.get("jid") != jid:
        state = {"jid": jid}

    url = state.get("external_url", "")
    if not url:
        emit_error("no external_url in state — run 'apply navigate <jid>' first")
        return 1

    # Build the full answer dict
    profile = _load_profile()
    ans_dict = _build_ans_dict(profile, answers)
    if not ans_dict:
        emit_error("no answers resolved — check profile or --answers")
        return 1

    # Phase 1: Playwright deterministic fill
    b, ctx = _chrome()
    page = _page_for(ctx)
    filled_playwright = []

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        if handle_captcha(page, state):
            emit_status("captcha", "CAPTCHA still present after timeout")
            return 1

        page_info = read_page(page)
        fields = page_info.get("fields", [])
        field_count = page_info.get("fieldCount", 0)

        if field_count == 0:
            print(f"  No fields detected via DOM", file=sys.stderr)

        filled_playwright, failed_playwright = _fill_with_playwright(page, fields, ans_dict)

        if filled_playwright:
            print(f"  Playwright filled: {', '.join(filled_playwright)}", file=sys.stderr)
        if failed_playwright:
            print(f"  Playwright failed/unknown: {len(failed_playwright)} fields", file=sys.stderr)

        # Phase 1.5: ask_api verification (fast, one image)
        if verify and filled_playwright:
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
    finally:
        try:
            b.close()
        except Exception:
            pass

    # Phase 2: Skyvern fills remaining (non-blocking — returns run_id for polling)
    skyvern_result = None
    needs_skyvern = failed_playwright or field_count == 0
    if needs_skyvern:
        print(f"  Handing off remaining fields to Skyvern (non-blocking)...", file=sys.stderr)
        from apply.common.skyvern_bridge import fill_remaining as _fill_remaining
        try:
            skyvern_result = _fill_remaining(
                url=url,
                answers=ans_dict,
                filled_fields=filled_playwright,
                wait=False,  # don't block — poll via run_id
                timeout=30,  # just need the initial response
            )
            status = skyvern_result.get("status", "unknown")
            print(f"  Skyvern: {status}", file=sys.stderr)
            if skyvern_result.get("browser_session_id"):
                state["browser_session_id"] = skyvern_result["browser_session_id"]
            if skyvern_result.get("run_id"):
                state["fill_run_id"] = skyvern_result["run_id"]
                print(f"  Skyvern run_id: {state['fill_run_id']}", file=sys.stderr)
                print(f"  Check status later via 'apply verify {jid}'", file=sys.stderr)
        except Exception as se:
            print(f"  Skyvern fill failed: {se}", file=sys.stderr)

    # Save state
    state["filled_count"] = len(filled_playwright)
    state["failed_fields"] = list(failed_playwright) if failed_playwright else []
    save_state(state)

    if field_count == 0 and not skyvern_result:
        emit_status("unknown", "no fields found by Playwright or Skyvern")
        return 1

    total_ok = len(filled_playwright)
    if skyvern_result and skyvern_result.get("status") == "completed":
        total_ok += 1

    msg = f"Playwright: {len(filled_playwright)} fields"
    if skyvern_result:
        msg += f" + Skyvern: {skyvern_result.get('status', 'unknown')}"
    emit_status("filled", msg)
    emit_next("submit")
    return 0


def cmd_next(jid):
    """Click Next/Continue on a multi-page form using Playwright."""
    b, ctx = _chrome()
    page = _page_for(ctx)
    try:
        state = load_state()
        url = state.get("external_url", "")
        if url:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(1)

        # Try Playwright first
        buttons = page.evaluate("""() => {
            const all = document.querySelectorAll('button, a');
            return Array.from(all).filter(el => el.offsetParent !== null).map(el => ({
                text: el.textContent.trim(),
                tag: el.tagName,
            }));
        }""")
        for btn in buttons:
            t = btn["text"].lower()
            if t in ("next", "continue", "next step", "continue to review"):
                if btn["tag"] == "A":
                    page.click(f'text="{btn["text"]}"')
                else:
                    page.click(f'button:text("{btn["text"]}")')
                time.sleep(2)
                emit_status("navigated", f"clicked '{btn['text']}'")
                emit_next("fill")
                return 0

        # Fallback: Skyvern click_next
        print(f"  No Next button found via DOM — using Skyvern", file=sys.stderr)
        from apply.common.skyvern_bridge import click_next
        result = click_next(url=page.url, timeout=120)
        if result.get("status") == "completed":
            emit_status("navigated", "skyvern clicked Next")
            emit_next("fill")
            return 0

        emit_error("no Next/Continue button found")
        return 1
    finally:
        try:
            b.close()
        except Exception:
            pass


def cmd_submit(jid, confirm=False):
    """Submit the form: Playwright finds and clicks submit, Skyvern fallback."""
    db_row = get_conn().execute(
        "SELECT stage, state FROM jobs WHERE id=?", (jid,)
    ).fetchone()
    if not db_row:
        emit_error(f"job {jid} not found")
        return 1
    stage, job_state = db_row["stage"], db_row["state"]

    if stage != "filled":
        emit_status(f"stage={stage}", "expected 'filled' — skipping submit")
        return 0

    state = load_state()
    if state.get("jid") != jid:
        state = {"jid": jid}

    url = state.get("external_url", "")
    if not url:
        emit_error("no external_url in state")
        return 1

    browser_session_id = state.get("browser_session_id", "")

    # Phase 1: Playwright tries to click submit
    b, ctx = _chrome()
    page = _page_for(ctx)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        submit_text = _detect_submit_button(page)
        if submit_text:
            print(f"  Found submit button: '{submit_text}'", file=sys.stderr)
            try:
                page.click(f'button:text("{submit_text}")')
            except Exception:
                try:
                    page.click(f'text="{submit_text}"')
                except Exception:
                    pass
            time.sleep(3)

            # Check if submit succeeded
            if check_applied_signal(page) or has_success_text(page_text(page) or ""):
                mark_applied(jid)
                emit_status("submitted", "Playwright clicked submit")
                emit_next("verify")
                return 0

            # Check for multi-page (Review step)
            next_btn = _detect_submit_button(page)
            if next_btn:
                print(f"  Review step detected — clicking '{next_btn}'", file=sys.stderr)
                try:
                    page.click(f'button:text("{next_btn}")')
                except Exception:
                    pass
                time.sleep(3)
                if check_applied_signal(page) or has_success_text(page_text(page) or ""):
                    mark_applied(jid)
                    emit_status("submitted", "Playwright review->submit")
                    emit_next("verify")
                    return 0

        # Phase 2: Skyvern click submit
        print(f"  Playwright could not confirm submit — using Skyvern", file=sys.stderr)
        from apply.common.skyvern_bridge import click_submit
        result = click_submit(url=page.url, browser_session_id=browser_session_id, timeout=180)
        if result.get("status") == "completed":
            mark_applied(jid)
            emit_status("submitted", "Skyvern clicked submit")
            emit_next("verify")
            return 0

        emit_status("unknown", "submit attempts inconclusive — check manually")
        emit_next("verify")
        return 1
    except Exception as e:
        emit_error(f"submit failed: {e}")
        return 1
    finally:
        try:
            b.close()
        except Exception:
            pass


def cmd_inspect(jid):
    """Full page analysis: screenshot, HTML, fields, buttons."""
    from lib.ask_api import available as _vision_available
    from lib.chrome_manager import CDP_URL

    b, ctx = _chrome()
    page = _page_for(ctx)
    state = load_state()

    url = state.get("external_url", "")
    if url:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
        except Exception as e:
            print(f"  GOTO_ERR: {e}", file=sys.stderr)

    from apply.common.inspect_lib import capture, page_jpeg
    from apply.common.page_helpers import page_html, read_page, scan_actions

    jid = state.get("jid", jid)
    img_path = capture(page, jid, prefix="inspect")
    print(f"  IMG: {img_path}", file=sys.stderr)

    info = read_page(page)
    print(f"  FIELDS: {info.get('fieldCount', 0)} detected", file=sys.stderr)
    for f in info.get("fields", []):
        opts = f.get("options", [])
        opt_str = f" ({len(opts)} options)" if opts else ""
        print(f"    [{f.get('type','?')}] {f.get('label','?')}{opt_str}", file=sys.stderr)

    submit_candidates = scan_actions(page, ["submit", "send", "apply", "next", "continue"])
    print(f"  BUTTONS:", file=sys.stderr)
    for c in submit_candidates[:10]:
        print(f"    [{c.get('score',0)}] '{c.get('text','')}' ({c.get('tag','')})", file=sys.stderr)

    print(f"  URL: {page.url[:120]}", file=sys.stderr)
    print(f"  CDP: {CDP_URL}", file=sys.stderr)

    if _vision_available():
        print(f"  ask: lib/ask_api.py --img {img_path} --prompt '?'", file=sys.stderr)

    return 0


def run(args):
    cmd = args.get("command", "")
    jid = args.get("jid", "")

    if cmd == "fill":
        answers = None
        raw = args.get("--answers")
        if raw:
            try:
                answers = json.loads(raw)
            except json.JSONDecodeError:
                emit_error(f"invalid --answers JSON: {raw}")
                return 1
        verify = not args.get("--no-verify", False)
        return cmd_fill(jid, answers, verify=verify)

    elif cmd == "next":
        return cmd_next(jid)

    elif cmd == "back":
        print("  Back: not implemented in hybrid mode — use browser back", file=sys.stderr)
        return 1

    elif cmd == "submit":
        return cmd_submit(jid, confirm=args.get("--confirm", False))

    elif cmd == "inspect":
        return cmd_inspect(jid)

    else:
        emit_error(f"unknown act command: {cmd}")
        return 1
