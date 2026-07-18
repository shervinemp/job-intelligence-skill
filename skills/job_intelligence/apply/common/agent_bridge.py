"""agent_bridge.py — Python-side wrapper for the injected window.__opencode agent.

The agent is auto-injected at the browser-context level by chrome_manager.connect()
via add_init_script, so every page gets it. These bridge functions call into it.

If the agent isn't available (dead page, edge case), every function uses optional
chaining — returns {} or [] — and the pipeline falls through to legacy strategies.
No regressions.
"""
import json


def get_framework(page) -> dict:
    """Detect frameworks loaded on the page."""
    return page.evaluate("window.__opencode?.detectFramework()") or {}


def get_fields(page) -> list[dict]:
    """Get all detected form fields (uses cached MutationObserver result)."""
    return page.evaluate("window.__opencode?.getFields()") or []


def set_value(page, selector: str, value: str) -> dict:
    """Set a field value using the framework-aware setter.
    Returns {ok: bool, oldVal: str, newVal: str, error: str}."""
    result = page.evaluate(
        f"window.__opencode?.setValue({json.dumps(selector)}, {json.dumps(str(value))})"
    )
    return result or {"ok": False, "error": "agent not available"}


def click(page, selector: str) -> dict:
    """Click an element with disabled re-enable.
    Returns {ok: bool, tag: str, text: str, error: str}."""
    result = page.evaluate(
        f"window.__opencode?.click({json.dumps(selector)})"
    )
    return result or {"ok": False, "error": "agent not available"}


def fill_autocomplete(page, field_label: str, value: str) -> dict:
    """Fill an autocomplete/search dropdown (Province, Country, etc.).
    Clicks the dropdown button, types the search term, selects the matching option.
    Returns {ok: bool, field: str, value: str, error: str}."""
    result = page.evaluate(
        f"window.__opencode?.fillAutocomplete({json.dumps(field_label)}, {json.dumps(value)})"
    )
    return result or {"ok": False, "error": "agent not available"}


def drain_value_log(page) -> list[dict]:
    """Get and clear the value change log since last drain.
    Each entry: {selector, label, oldVal, newVal, ts, trusted}."""
    return page.evaluate("window.__opencode?.drainValueLog()") or []


def drain_submit_log(page) -> list[dict]:
    """Get and clear the XHR/fetch submit log since last drain.
    Each entry: {type, url, ts, status, body, success}."""
    return page.evaluate("window.__opencode?.drainSubmitLog()") or []


def drain_console_errors(page) -> list[dict]:
    """Get and clear captured console errors since last drain.
    Each entry: {msg, ts}."""
    return page.evaluate("window.__opencode?.drainConsoleErrors()") or []


def invalidate_fields(page):
    """Force field rescanner on next getFields call."""
    page.evaluate("window.__opencode?.invalidateFields()")


def setup_network_interception(page):
    """Set up Playwright route interception for form submission detection.
    Unlike JS monkeypatching, this doesn't touch the page's JS at all —
    no fetch.toString() fingerprinting risk."""
    def _handle(route, request):
        is_submit = request.method in ("POST", "PUT")
        if is_submit:
            try:
                resp = route.fetch()
                body = resp.body().decode("utf-8", errors="replace")[:500]
                success = resp.ok and "error" not in body.lower() and "invalid" not in body.lower()
                import time as _t
                _entry = json.dumps({
                    "type": request.resource_type,
                    "url": request.url[:150],
                    "method": request.method,
                    "status": resp.status,
                    "body": body[:200],
                    "success": success,
                    "ts": _t.time(),
                })
                page.evaluate(f"window.__opencode?.recordSubmit({_entry})")
                route.fulfill(response=resp)
            except Exception:
                route.continue_()
        else:
            route.continue_()
    try:
        page.route("**/*", _handle)
    except Exception:
        pass


def submit_report(page) -> dict:
    """Quick check: did a recent form submission appear to succeed?
    Returns the most recent submit log entry, or None."""
    logs = drain_submit_log(page)
    if not logs:
        return None
    latest = logs[-1]
    errors = drain_console_errors(page)
    if errors:
        latest["console_errors"] = errors
    return latest
