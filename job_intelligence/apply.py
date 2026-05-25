"""apply.py — LLM-in-the-middle auto-apply. Usage: python apply.py auto <jid>"""

import json, os, sys, time

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_PATH = os.path.join(SKILL_DIR, "profile.json")
SECRETS_PATH = os.path.join(SKILL_DIR, "secrets.json")

from lib.db import load, save, advance
from lib.db import get_job, app_list, app_get
from lib.call_gemini import call_gemini_node


def _load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _fmt_fields(fields):
    lines = []
    for i, f in enumerate(fields, 1):
        label = f.get("label") or f.get("placeholder") or f.get("name") or f.get("id")
        dtype = f.get("type") or f.get("tag", "")
        req = " *" if f.get("required") else ""
        file_upload = " [FILE]" if f.get("tag") == "file" else ""
        opts = ""
        if f.get("options"):
            opts = " [" + ", ".join(f["options"][:8]) + "]"
        lines.append(f"{i}. [{dtype}]{file_upload} \"{label}\"{req}{opts}")
    return "\n".join(lines)


def _fmt_profile(profile):
    parts = []
    for k in ("first_name", "last_name", "email", "phone", "location", "linkedin", "portfolio", "github"):
        v = profile.get(k, "")
        if v:
            parts.append(f"{k}: {v}")
    for exp in profile.get("work_experience", []):
        line = f"{exp.get('title', '')} @ {exp.get('company', '')}"
        if exp.get("start_date"):
            line += f" ({exp['start_date']} - {exp.get('end_date', 'present')})"
        if exp.get("description"):
            line += f" — {exp['description'][:200]}"
        parts.append(line)
    for edu in profile.get("education", []):
        line = f"{edu.get('degree', '')} @ {edu.get('school', '')}"
        if edu.get("graduation"):
            line += f" ({edu['graduation']})"
        parts.append(line)
    if profile.get("skills"):
        parts.append(f"skills: {', '.join(profile['skills'][:10])}")
    ca = profile.get("common_answers", {})
    for k, v in ca.items():
        if v:
            parts.append(f"{k}: {v}")
    return "\n".join(parts)


FORM_PROMPT = """Fill this job application form using my data. Return ONLY a JSON array.

JOB: {title} @ {company}

MY DATA:
{profile}

FORM FIELDS:
{fields}

JSON: [{{"sel":"css-selector","val":"value"}}]
Skip unknown fields (empty string -> skip entirely). No explanation."""

LOGIN_PROMPT = """Fill this login form. Return ONLY a JSON array.

Credentials for {domain}:
email: {email}
password: {password}

FORM FIELDS:
{fields}

JSON: [{{"sel":"css-selector","val":"value"}}]
No explanation."""


def _extract_form(page):
    return page.evaluate("""() => {
        const fields = [];
        document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea, [role=combobox] input, input[type=file]').forEach(el => {
            if (el.offsetParent === null && !el.closest('[role=dialog]')) return;
            const label = el.closest('label') || document.querySelector('label[for="' + el.id + '"]');
            const parent = el.closest('div, fieldset, section, li, [role=dialog]');
            const labelText = label ? label.textContent.trim() :
                parent ? (parent.querySelector('label, .label, legend, [role=heading]')?.textContent?.trim() || '') : '';
            const opts = el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean) : [];
            fields.push({
                tag: el.tagName.toLowerCase(),
                type: el.getAttribute('type') || '',
                name: el.getAttribute('name') || '',
                id: el.id || '',
                label: labelText || el.getAttribute('placeholder') || el.getAttribute('aria-label') || '',
                required: el.required || el.getAttribute('aria-required') === 'true',
                selector: el.id ? '#' + el.id : el.name ? '[name="' + el.name.replace(/"/g,'\\\\"') + '"]' : '',
                options: opts,
                value: el.value || '',
            });
        });
        return fields;
    }""")


def _click_action(page):
    return page.evaluate("""() => {
        const btns = document.querySelectorAll('button:not([disabled]), input[type=submit]:not([disabled])');
        const keywords = ['submit','next','continue','save','apply','review','post','send','done','finish','confirm','proceed'];
        let best = null, bestScore = 0;
        btns.forEach(b => {
            const t = (b.textContent || b.value || '').toLowerCase().trim();
            let score = 0;
            keywords.forEach(k => { if (t.includes(k)) score += t === k ? 10 : 5; });
            if (t.includes('easy')) score -= 5;
            if (b.classList.contains('artdeco-button--primary')) score += 3;
            if (b.getAttribute('type') === 'submit') score += 1;
            if (score > bestScore) { bestScore = score; best = b; }
        });
        if (best) { best.click(); return true; }
        return false;
    }""")


def _check_success(page):
    try:
        text = page.evaluate("document.body.innerText").lower()
        for w in ["thank you", "application submitted", "we received",
                   "your application", "was sent", "has been submitted",
                   "application received"]:
            if w in text:
                return True
    except Exception:
        pass
    return False


def _detect_auth(page):
    try:
        text = page.evaluate("document.body.innerText").lower()
        url = page.url.lower()
        if any(d in url for d in ["/login", "/auth", "signin", "authwall"]):
            return True
        login_phrases = ["sign in to view", "sign in to see", "join now to see",
                         "create account to view", "please sign in", "log in to continue"]
        if any(p in text for p in login_phrases):
            return True
    except Exception:
        pass
    return False


def _execute_plan(page, plan, resume_path=None):
    for item in plan:
        sel = item.get("sel")
        val = item.get("val", "")
        if not sel:
            continue
        try:
            el = page.query_selector(sel)
            if not el:
                continue
            tag = el.evaluate("el.tagName.toLowerCase() + (el.getAttribute('type') ? ':' + el.type : '')")
            if tag in ("input:file",):
                if resume_path and os.path.exists(resume_path):
                    el.set_input_files(resume_path)
                continue
            if tag.startswith("select") or el.evaluate("el.getAttribute('role')") == "combobox":
                if val:
                    el.select_option(val)
            elif el.evaluate("el.type") in ("checkbox", "radio"):
                is_checked = val.lower() in ("yes", "true", "1", "on")
                if el.is_checked() != is_checked:
                    el.click()
            elif val:
                el.fill(val)
        except Exception:
            pass


def _click_easy_apply(page):
    clicked = page.evaluate("""() => {
        const btns = document.querySelectorAll('button, a');
        for (const b of btns) {
            const t = (b.textContent || '').toLowerCase().trim();
            if (t.includes('easy apply') && !b.disabled && b.offsetParent !== null) {
                b.click();
                return true;
            }
        }
        return false;
    }""")
    if clicked:
        page.wait_for_timeout(3000)
    return clicked


def _form_hash(fields):
    return hash(tuple(sorted((f.get("name", ""), f.get("id", ""), f.get("value", "")) for f in fields)))


def _find_resume(jid, profile):
    path = profile.get("resume_path", "")
    if path and os.path.exists(path):
        return path
    for f in app_list(jid):
        if f["filename"].endswith(".pdf"):
            p = os.path.join(tempfile.gettempdir(), "job_intelligence", jid, f["filename"])
            content = app_get(jid, f["filename"])
            if content:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as fh:
                    fh.write(content.encode() if isinstance(content, str) else content)
                return p
    return None


def auto_apply(jid):
    job = get_job(jid)
    if not job:
        return f"BAILED:{jid}:not_found"
    url = job.get("url", "")
    if not url:
        return f"BAILED:{jid}:no_url"
    title = job.get("title", "")
    company = job.get("company", "")

    profile = _load_json(PROFILE_PATH)
    secrets = _load_json(SECRETS_PATH)
    if not profile.get("email"):
        return f"BAILED:{jid}:no_profile"

    resume = _find_resume(jid, profile)

    from lib.chrome_manager import connect

    b, ctx = connect()
    if not ctx:
        return f"BAILED:{jid}:chrome_not_available"
    page = ctx.new_page()

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        _click_easy_apply(page)
        last_hash = None

        for step in range(8):
            page.wait_for_timeout(1500)

            if _check_success(page):
                return f"APPLIED:{jid}"

            if _detect_auth(page):
                domain = page.url.split("/")[2] if len(page.url.split("/")) > 2 else ""
                creds = secrets.get("login_credentials", {}).get(domain, {})
                if creds.get("email") and creds.get("password"):
                    fields = _extract_form(page)
                    prompt = LOGIN_PROMPT.format(
                        domain=domain, email=creds["email"],
                        password=creds["password"],
                        fields=_fmt_fields(fields),
                    )
                    ok, out = call_gemini_node(prompt, timeout_seconds=30)
                    if ok:
                        try:
                            plan = json.loads(out) if isinstance(out, str) else out
                            _execute_plan(page, plan if isinstance(plan, list) else [plan])
                            page.wait_for_timeout(2000)
                        except Exception:
                            pass
                return f"BAILED:{jid}:login_needed"

            fields = _extract_form(page)
            if not fields:
                return f"BAILED:{jid}:no_form_fields"

            prompt = FORM_PROMPT.format(
                title=title, company=company,
                profile=_fmt_profile(profile),
                fields=_fmt_fields(fields),
            )

            for attempt in range(2):
                ok, out = call_gemini_node(prompt, timeout_seconds=45)
                if ok:
                    break
            if not ok:
                return f"BAILED:{jid}:llm:{str(out)[:60]}"

            try:
                plan = json.loads(out) if isinstance(out, str) else out
                if not isinstance(plan, list):
                    plan = [plan]
            except json.JSONDecodeError:
                return f"BAILED:{jid}:bad_json"

            _execute_plan(page, plan, resume_path=resume)
            page.wait_for_timeout(1000)

            if not _click_action(page):
                return f"BAILED:{jid}:no_button"
            page.wait_for_timeout(3000)

            if _check_success(page):
                return f"APPLIED:{jid}"

            new_hash = _form_hash(_extract_form(page))
            if new_hash == last_hash:
                return f"BAILED:{jid}:stuck"
            last_hash = new_hash

        return f"BAILED:{jid}:max_steps"
    except Exception as e:
        return f"BAILED:{jid}:error:{str(e)[:80]}"
    finally:
        try:
            b.close()
        except Exception:
            pass


def cmd_auto(jid):
    result = auto_apply(jid)
    print(result, file=sys.stderr)
    print(result)
    if result.startswith("APPLIED"):
        state = load()
        if jid in state.get("jobs", {}):
            advance(state["jobs"][jid], "applied",
                    applied_at=time.strftime("%Y-%m-%dT%H:%M:%S"))


def cmd_batch(count=1):
    state = load()
    tailored = [(jid, e) for jid, e in state["jobs"].items()
                if e.get("stage") == "tailored"]
    if not tailored:
        print("NO_TAILORED", file=sys.stderr)
        return
    applied = bailed = 0
    for jid, entry in tailored[:count]:
        print(f"  {entry.get('title')} @ {entry.get('company')}", file=sys.stderr)
        r = auto_apply(jid)
        print(f"  {r}", file=sys.stderr)
        if r.startswith("APPLIED"):
            state = load()
            if jid in state.get("jobs", {}):
                advance(state["jobs"][jid], "applied",
                        applied_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
                applied += 1
        else:
            bailed += 1
    print(f"APPLIED:{applied} BAILED:{bailed}", file=sys.stderr)


def cmd_help():
    print("Usage:", file=sys.stderr)
    print("  auto <jid>          Auto-apply for a specific job", file=sys.stderr)
    print("  batch [--count N]   Batch apply (default 1)", file=sys.stderr)
    print("  help                This message", file=sys.stderr)


def main():
    if len(sys.argv) < 3 and not (len(sys.argv) == 2 and sys.argv[1] == "help"):
        print("Usage: python apply.py auto <jid> | batch [--count N] | help", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "auto":
        cmd_auto(sys.argv[2])
    elif cmd == "batch":
        count = 1
        if "--count" in sys.argv:
            i = sys.argv.index("--count")
            if i + 1 < len(sys.argv):
                count = int(sys.argv[i + 1])
        cmd_batch(count)
    elif cmd == "help":
        cmd_help()
    else:
        print(f"Unknown: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
