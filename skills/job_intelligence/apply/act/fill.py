"""act/fill.py — Hybrid fill command: Playwright-first, Skyvern-fallback."""
import json, os, sys, time

from lib.db import get_conn
from apply.common.output import emit_next, emit_status, emit_error, emit_fill_report
from apply.common.page_helpers import load_state, save_state, handle_captcha, handle_session_timeout, tag_page
from apply.act.helpers import (
    _load_profile, _chrome, _page_for, _host, _is_error_page, _url_fallbacks,
    _wait_for_fields, _probe_form, _fill_with_playwright, _find_next_button,
    _empty_required, _click_action, _verify_with_ask_api, _detect_submit_button,
    _field_key, _build_ans_dict,
)


def cmd_fill(jid, answers: dict = None, verify: bool = True, max_pages: int = 4,
             quick: bool = False):
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
    state["jid"] = jid

    url = state.get("external_url") or state.get("url", "")
    if not url:
        emit_error("no external_url in state — run 'apply navigate <jid>' first")
        return 1
    orig_url = url

    profile = _load_profile()
    ans_dict = _build_ans_dict(profile, answers)
    if not ans_dict:
        emit_error("no answers resolved — check profile or --answers")
        return 1

    from apply.common.registry import resolve as resolve_registry

    b, ctx = _chrome()
    filled_all, failed_all = [], []
    filled_keys = set()
    field_total = 0
    submit_visible = False

    try:
        page = _page_for(ctx, state)
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
        reg = resolve_registry(page.url) or resolve_registry(orig_url)
        if reg and reg.page_range:
            try:
                max_pages = min(max_pages, int(reg.page_range[-1]))
            except Exception:
                pass
        _wait_for_fields(page, timeout=8)

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
    finally:
        try:
            b.close()
        except Exception:
            pass

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
        emit_next("submit")
    else:
        emit_next("act --inspect", "no fillable fields and no Skyvern run")
    return 0
