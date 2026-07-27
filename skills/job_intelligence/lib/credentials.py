"""lib/credentials.py — Credential vault for job application sites.

Primary: OS keychain (Windows Credential Manager via keyring).
Fallback: plaintext JSON in ~/.ji/credentials.json (gitignored, user-owned).

Per-site credentials keyed by domain, supporting multiple password
candidates per domain (auth attempts try all in order). Also stores a
shared pool of 'standard passwords' that the user typically uses, so
account-creation can pick a password that best fits platform-specific
complexity rules.

Entry shape:
    {
      "email": "...",
      "password": "primary password",
      "passwords": ["alt1", "alt2", ...]   # optional, tried after primary
    }

Shared 'standard' passwords entry (key = "shared"):
    {
      "passwords": ["pw A", "pw B", ...]
    }

Account creation defaults sourced from profile.json.

Safe password entry (no shell history, no echo):
    python apply.py creds set <domain> <email>          # prompts via getpass
    python apply.py creds add-pw <domain>               # prompts via getpass
    python apply.py creds shared-add                     # prompts via getpass
    python apply.py creds shared-set                     # prompts via getpass
"""
import json
import os
import re
import secrets
import string
import sys

_SERVICE = "job-intelligence"
_SHARED_KEY = "shared"  # keyring entry for shared/common passwords

_FALLBACK_PATH = os.path.join(
    os.environ.get("JI_HOME", os.path.join(os.path.expanduser("~"), ".ji")),
    "credentials.json",
)


def _domain_from_url(url):
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    if len(parts) > 2:
        return ".".join(parts[-3:])
    return host


def _prompt_passwords():
    """Get password(s) from user via getpass — no echo, no shell history.

    Prompts for the primary password, then optionally more candidates.
    Press Enter without typing to stop adding. Returns a list of
    non-empty passwords (may be empty if user pressed Enter immediately).
    """
    import getpass
    pws = []
    try:
        print("Enter passwords (blank line to finish). Input is hidden.", file=sys.stderr)
        while True:
            label = "  password" if not pws else f"  alt password #{len(pws)+1}"
            pw = getpass.getpass(label + ": ")
            if not pw:
                break
            pws.append(pw)
    except (KeyboardInterrupt, EOFError):
        print(file=sys.stderr)
    return pws


def _fallback_read():
    try:
        with open(_FALLBACK_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _fallback_write(data):
    os.makedirs(os.path.dirname(_FALLBACK_PATH), exist_ok=True)
    with open(_FALLBACK_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _keyring_get(key):
    try:
        import keyring
        raw = keyring.get_password(_SERVICE, key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _keyring_set(key, data):
    try:
        import keyring
        keyring.set_password(_SERVICE, key, json.dumps(data))
        return True
    except Exception:
        return False


def _read_entry(domain):
    """Read raw entry dict (no coercion)."""
    domain = domain.lower()
    data = _keyring_get(domain)
    if data is not None:
        return data
    fb = _fallback_read()
    return fb.get(domain, {})


def _write_entry(domain, data):
    domain = domain.lower()
    if not _keyring_set(domain, data):
        fb = _fallback_read()
        fb[domain] = data
        _fallback_write(fb)


def get_creds(domain):
    """Return {"email": ..., "password": str, "passwords": [str, ...]} or None.

    `password` is the primary (first in passwords list if present);
    `passwords` is a list of unique password candidates (primary first).
    Falls back to shared pool + any stored legacy passwords for the
    domain to maximise the chance of correct auth on a noisy ATS.
    """
    entry = _read_entry(domain)
    if not entry or not entry.get("email"):
        return None

    pws = []
    if entry.get("password"):
        pws.append(entry["password"])
    for p in (entry.get("passwords") or []):
        if p and p not in pws:
            pws.append(p)

    # Pull shared passwords as additional candidates for trial signing-in
    shared = _read_entry(_SHARED_KEY) or {}
    for p in (shared.get("passwords") or []):
        if p and p not in pws:
            pws.append(p)

    if not pws:
        return None
    return {
        "email": entry["email"],
        "password": pws[0],
        "passwords": pws,
    }


def save_creds(domain, email, password, passwords=None):
    """Save creds for a domain.

    Args:
        password: primary password (always stored on top)
        passwords: optional additional candidates. If omitted, keeps
                   any existing alternates.
    """
    domain = domain.lower()
    entry = _read_entry(domain) or {"email": email}
    entry["email"] = email
    entry["password"] = password
    if passwords is not None:
        # Build ordered unique list, primary first
        seen = {password}
        ordered = [password]
        for p in passwords:
            if p and p not in seen:
                ordered.append(p)
                seen.add(p)
        entry["passwords"] = ordered
    _write_entry(domain, entry)


def add_password(domain, password):
    """Append a password candidate to a domain's vault entry."""
    domain = domain.lower()
    entry = _read_entry(domain) or {}
    pws = entry.get("passwords") or ([entry["password"]] if entry.get("password") else [])
    if password and password not in pws:
        pws.append(password)
    entry["passwords"] = pws
    if not entry.get("password") and pws:
        entry["password"] = pws[0]
    _write_entry(domain, entry)


def get_shared_passwords():
    """Return list of password patterns the user typically uses."""
    shared = _read_entry(_SHARED_KEY) or {}
    return list(shared.get("passwords") or [])


def set_shared_passwords(passwords):
    """Replace the shared password pool."""
    _write_entry(_SHARED_KEY, {"passwords": list(dict.fromkeys(passwords))})


def add_shared_password(password):
    cur = get_shared_passwords()
    if password and password not in cur:
        cur.append(password)
        set_shared_passwords(cur)


def has_creds(domain):
    return get_creds(domain) is not None


def get_account_defaults():
    """Return profile fields useful for account creation."""
    profile_path = os.path.join(os.path.dirname(__file__), "..", "profile.json")
    try:
        with open(profile_path, encoding="utf-8") as f:
            p = json.load(f)
        return {
            "email": p.get("email", ""),
            "first_name": p.get("first_name", ""),
            "last_name": p.get("last_name", ""),
            "phone": p.get("phone", ""),
            "zip": p.get("zip", ""),
            "location": p.get("location", ""),
            "linkedin_url": p.get("linkedin_url", ""),
        }
    except (OSError, json.JSONDecodeError):
        return {}


# Password complexity rules typically enforced by ATS platforms
def gen_password(length=16):
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alphabet) for _ in range(length))


_RULES = {
    "min_length": {"workday": 8, "default": 8},
    "require_upper": {"workday": True, "default": True},
    "require_lower": {"workday": True, "default": True},
    "require_digit": {"workday": True, "default": True},
    "require_symbol": {"workday": True, "default": False},
}


def _platform_key(url):
    host = (url or "").lower()
    if "workday" in host:
        return "workday"
    return "default"


def _score_password_complexity(pw):
    """Return a complexity score: min length, upper, lower, digit, symbol."""
    s = 0
    for cond in (
        len(pw) >= 8,
        len(pw) >= 12,
        bool(re.search(r'[A-Z]', pw)),
        bool(re.search(r'[a-z]', pw)),
        bool(re.search(r'\d', pw)),
        bool(re.search(r'[^A-Za-z0-9]', pw)),
    ):
        if cond:
            s += 1
    return s


def pick_password_for_platform(url, existing_pws, page=None):
    """Pick the best existing password that satisfies the target
    platform's password complexity rules.

    Rules are gathered in this order of priority:
      1. Deterministic parser — scans page text for known instructions.
      2. Local LLM (ask_api) — if deterministic finds nothing AND the
         local model is available, sends page text + asks for rules JSON.
      3. Hardcoded `_RULES` table — used as implicit minimum when
         other sources don't supply a field.

    Returns None when no input password fits the resolved rules.

    Args:
        url: URL of the target page (drives _platform_key).
        existing_pws: ordered candidate passwords (primary first).
        page: optional Playwright page; page=None uses hardcoded rules only.
    """
    if not existing_pws:
        return None

    # Step 1: Try deterministic page-text extraction.
    hints = _extract_password_hints_deterministic(page) if page else None

    # Step 2: LLM fallback for rule extraction.
    if not hints and page is not None:
        hints = _extract_password_hints_via_llm(page)

    # Step 3: Merge with hardcoded _RULES defaults so that every
    # platform gets at least the baseline requirements.
    plat = _platform_key(url)
    def _rule(field, key):
        if hints and hints.get(key) is not None:
            return hints[key]
        return _RULES[field].get(plat, _RULES[field]["default"])

    min_len = (hints or {}).get("min_len") or _RULES["min_length"].get(plat, _RULES["min_length"]["default"])
    require_sym = _rule("require_symbol", "require_sym")
    require_digit = _rule("require_digit", "require_digit")
    require_upper = _rule("require_upper", "require_upper")
    require_lower = _rule("require_lower", "require_lower")

    # Filter acceptable passwords.
    acceptables = []
    for pw in existing_pws:
        if len(pw) < min_len:
            continue
        if require_sym and not re.search(r'[^A-Za-z0-9]', pw):
            continue
        if require_digit and not re.search(r'\d', pw):
            continue
        if require_upper and not re.search(r'[A-Z]', pw):
            continue
        if require_lower and not re.search(r'[a-z]', pw):
            continue
        acceptables.append(pw)
    if acceptables:
        # Pick the most complex acceptable password (most stable).
        return max(acceptables, key=_score_password_complexity)
    return None


def _extract_password_hints_deterministic(page):
    """Pull password complexity hints from visible page text using regex.

    Returns dict: {min_len, require_upper, require_lower, require_digit, require_sym}
    or None when no relevant text is found.
    """
    if page is None:
        return None
    try:
        txt = page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('label, p, div, small, span'));
            return els.map(e => (e.textContent || '').trim())
                .filter(t => t.length > 0 && t.length < 500)
                .join('\\n');
        }""")
    except Exception:
        return None
    if not txt:
        return None
    lower = txt.lower()
    min_len = 0
    m = re.search(r'(?:min(?:imum)?|at least)\s*(\d{1,2})\s*(?:char|character|letter)', lower)
    if not m:
        m = re.search(r'\b(\d{1,2})\s*(?:char|character|letter)\s*min(?:imum)?', lower)
    if not m:
        m = re.search(r'\b(\d{1,2})\s*(?:or more|minimum)', lower)
    if m:
        min_len = int(m.group(1))
    require_digit = bool(re.search(r'(?:at least|require|must contain|[a-z]\s+and)\s+(?:\ba\b|1+)\s*(?:numeric|digit|number)', lower)) or "number" in lower
    require_upper = bool(re.search(r'(?:at least|require|must contain)\s+(?:\ba\b|1+)\s*(?:uppercase|capital|upper-case)', lower))
    require_lower = bool(re.search(r'(?:at least|require|must contain)\s+(?:\ba\b|1+)\s*(?:lowercase|lower-case)', lower))
    require_sym = bool(re.search(r'(?:at least|require|must contain)\s+(?:\ba\b|1+)\s*(?:special|symbol|non-alphanumeric|punctuation)', lower)) or "special character" in lower
    if not (min_len or require_digit or require_upper or require_lower or require_sym):
        return None
    return {
        "min_len": min_len,
        "require_upper": require_upper,
        "require_lower": require_lower,
        "require_digit": require_digit,
        "require_sym": require_sym,
    }


def _extract_password_hints_via_llm(page):
    """LLM fallback: ask local ask_api to extract password rules from
    visible page text. Returns the same dict shape as the deterministic
    parser, or None on any failure / unavailability.
    """
    if page is None:
        return None
    try:
        from lib import ask_api
        if not ask_api.available():
            return None
    except Exception:
        return None
    try:
        txt = page.evaluate("() => document.body.innerText.slice(0, 4000)")
    except Exception:
        return None
    if not txt or "password" not in txt.lower():
        return None
    prompt = (
        "Below is the visible page text from a job-application site. "
        "Extract the password complexity requirements stated for password "
        "creation. Output ONE JSON line with keys: min_len (int or 0), "
        "require_upper (bool), require_lower (bool), require_digit (bool), "
        "require_sym (bool). If unknown, use 0/false. "
        "Only output the JSON line, no other text. "
        "Page text:\n\n" + (txt or "")[:2000]
    )
    try:
        from lib.ask_api import ask_text
        reply, err = ask_text(prompt, max_tokens=200)
        if err or not reply:
            return None
        import json as _json
        # Find JSON object in reply
        m = re.search(r'\{[^{}]+\}', reply)
        if not m:
            return None
        data = _json.loads(m.group(0))
        return {
            "min_len": int(data.get("min_len", 0) or 0),
            "require_upper": bool(data.get("require_upper", False)),
            "require_lower": bool(data.get("require_lower", False)),
            "require_digit": bool(data.get("require_digit", False)),
            "require_sym": bool(data.get("require_sym", False)),
        }
    except Exception:
        return None


def gen_password_for_platform(url, page=None, existing_pws=None):
    """Generate a fresh password for the target platform.

    Tries in order:
      1. Local LLM (ask_api) — given page rules (deterministic or LLM
         parsed) + a sample of existing shared passwords, asks the LLM
         to construct a new password in the *same style* while satisfying
         the platform's requirements.
      2. Secure generator — pure `secrets`-based password with length
         and rule satisfaction. Ensures validity even when ask_api /
         local LLM is unavailable.

    Args:
        url: Platform URL (used to pre-seed hardcoded _RULES).
        page: Optional Playwright page — used to read page text for
              rule extraction and to give the LLM platform-specific
              context.
        existing_pws: List of existing password strings — the LLM (if
              fired) uses these as *style* examples so the generated
              password resembles the user's previous passwords while
              fitting the new platform's rules.

    Returns:
        str: A password that satisfies platform complexity rules.
    """
    # Resolve rules same way as pick_password_for_platform.
    hints = _extract_password_hints_deterministic(page) if page else None
    if not hints and page is not None:
        hints = _extract_password_hints_via_llm(page)
    plat = _platform_key(url)
    min_len = (hints or {}).get("min_len") or _RULES["min_length"].get(plat, _RULES["min_length"]["default"])
    require_sym = (hints.get("require_sym") if hints else None) or _RULES["require_symbol"].get(plat, _RULES["require_symbol"]["default"])
    require_digit = (hints.get("require_digit") if hints else None) or _RULES["require_digit"].get(plat, _RULES["require_digit"]["default"])
    require_upper = (hints.get("require_upper") if hints else None) or _RULES["require_upper"].get(plat, _RULES["require_upper"]["default"])
    require_lower = (hints.get("require_lower") if hints else None) or _RULES["require_lower"].get(plat, _RULES["require_lower"]["default"])

    # Path 1: LLM-style generator. Uses existing patterns as examples.
    if existing_pws:
        new_pw = _gen_password_via_llm(
            existing_pws, min_len, require_upper, require_lower,
            require_digit, require_sym
        )
        if new_pw and _password_valid(new_pw, min_len, require_upper,
                                       require_lower, require_digit, require_sym):
            return new_pw

    # Path 2: secure generator. Guaranteed to satisfy the rules.
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(max(min_len, 16)))
        if _password_valid(pw, min_len, require_upper,
                            require_lower, require_digit, require_sym):
            return pw


def _password_valid(pw, min_len, require_upper, require_lower, require_digit, require_sym):
    """Check whether a password satisfies the resolved complexity rules."""
    if len(pw) < min_len:
        return False
    if require_upper and not re.search(r'[A-Z]', pw):
        return False
    if require_lower and not re.search(r'[a-z]', pw):
        return False
    if require_digit and not re.search(r'\d', pw):
        return False
    if require_sym and not re.search(r'[^A-Za-z0-9]', pw):
        return False
    return True


def _gen_password_via_llm(existing_pws, min_len, require_upper,
                          require_lower, require_digit, require_sym):
    """Use local ask_api LLM to generate a password in the same style as
    the existing shared passwords while satisfying new platform rules.

    The model receives a small sample (3-5 shared passwords) and an
    explicit rule specification, and is asked to emit ONE password.

    Returns None if ask_api is unavailable or the model returns
    something unusable (too short, fails rules, contains spaces).
    """
    try:
        from lib import ask_api
        if not ask_api.available():
            return None
    except Exception:
        return None
    # Take up to 5 examples, oldest/most-frequent first.
    samples = list(existing_pws)[:5]
    rules_list = [
        f"min length {min_len}",
        "include uppercase" if require_upper else "",
        "include lowercase" if require_lower else "",
        "include digit" if require_digit else "",
        "include special character" if require_sym else "",
    ]
    rules_list = [r for r in rules_list if r]
    prompt = (
        "You are generating a single new password for the user. "
        "It must satisfy these complexity rules:\n  - "
        + "\n  - ".join(rules_list)
        + "\n\nThe user already uses these passwords (do NOT reuse them, "
          "but mimic their style — capitalisation pattern, digit "
          "placement, symbol preference, length):\n"
        + "\n".join(f"  - {s}" for s in samples)
        + "\n\nOutput exactly ONE password on a single line. "
          "No quotes, no markers, no trailing newline. "
    )
    try:
        from lib.ask_api import ask_text
        reply, err = ask_text(prompt, max_tokens=200, temperature=0.7)
    except Exception:
        return None
    if err or not reply:
        return None
    # Take the first non-empty alphanumeric+symbol token in the reply.
    for ln in reply.strip().splitlines():
        cand = ln.strip().strip("`'\"")
        if cand and " " not in cand and 4 <= len(cand) <= 80:
            return cand
    return None


def cmd_creds(args):
    if not args:
        print(__doc__, file=sys.stderr)
        return
    cmd = args[0]
    if cmd == "list":
        fb = _fallback_read()
        if not fb:
            print("(no fallback credentials — keyring-only entries not listed)", file=sys.stderr)
        for domain in sorted(fb):
            entry = fb[domain]
            print(f"  {domain}: email={entry.get('email', '?')}, "
                  f"passwords={len(entry.get('passwords') or [entry.get('password')] or [])}")
        # Also list shared passwords count
        shared = _read_entry(_SHARED_KEY) or {}
        npw = len(shared.get("passwords") or [])
        print(f"  {_SHARED_KEY}: {npw} shared password(s)")
        return
    if cmd == "get":
        if len(args) < 2:
            print("Usage: creds get <domain>", file=sys.stderr)
            return
        c = get_creds(args[1])
        if c:
            print(f"  email: {c['email']}")
            for i, p in enumerate(c["passwords"]):
                marker = " (primary)" if i == 0 else ""
                print(f"  password[{i}]: {p}{marker}")
            print(f"  total candidates: {len(c['passwords'])}")
        else:
            print("  (not found)", file=sys.stderr)
        return
    if cmd == "set":
        # creds set <domain> <email> [pw1 pw2 ...]
        # Passwords omitted → interactive getpass prompt (safe entry).
        if len(args) < 3:
            print("Usage: creds set <domain> <email> [password ...]  (prompts if omitted)", file=sys.stderr)
            return
        domain = args[1]
        email = args[2]
        pws = args[3:] if len(args) > 3 else _prompt_passwords()
        if not pws:
            print("  no passwords entered — aborting", file=sys.stderr)
            return
        save_creds(domain, email, pws[0], passwords=pws)
        print(f"  saved {domain}: {email} ({len(pws)} password(s))", file=sys.stderr)
        return
    if cmd == "add-pw":
        # creds add-pw <domain> [password]
        if len(args) < 2:
            print("Usage: creds add-pw <domain> [password]  (prompts if omitted)", file=sys.stderr)
            return
        domain = args[1]
        pws = args[2:] if len(args) > 2 else _prompt_passwords()
        if not pws:
            print("  no passwords entered — aborting", file=sys.stderr)
            return
        for pw in pws:
            add_password(domain, pw)
        print(f"  added {len(pws)} password(s) to {domain}", file=sys.stderr)
        return
    if cmd == "shared-list":
        pws = get_shared_passwords()
        if not pws:
            print("  (no shared passwords)", file=sys.stderr)
        for i, p in enumerate(pws):
            print(f"  [{i}] {p}")
        return
    if cmd == "shared-add":
        # creds shared-add [password ...]
        pws = args[1:] if len(args) > 1 else _prompt_passwords()
        if not pws:
            print("  no passwords entered — aborting", file=sys.stderr)
            return
        for pw in pws:
            add_shared_password(pw)
        print(f"  shared pool now has {len(get_shared_passwords())} password(s)", file=sys.stderr)
        return
    if cmd == "shared-set":
        # creds shared-set [pw1 pw2 ...]  (interactive if omitted)
        pws = args[1:] if len(args) > 1 else _prompt_passwords()
        if not pws:
            print("  no passwords entered — aborting", file=sys.stderr)
            return
        set_shared_passwords(pws)
        print(f"  shared pool set to {len(get_shared_passwords())} password(s)", file=sys.stderr)
        return
    if cmd == "delete":
        if len(args) < 2:
            print("Usage: creds delete <domain>", file=sys.stderr)
            return
        try:
            import keyring
            keyring.delete_password(_SERVICE, args[1].lower())
        except Exception:
            pass
        fb = _fallback_read()
        fb.pop(args[1].lower(), None)
        _fallback_write(fb)
        print(f"  deleted {args[1]}", file=sys.stderr)
        return
    print(f"Unknown subcommand: {cmd}", file=sys.stderr)
    print("Commands: list, get, set, add-pw, shared-list, shared-add, shared-set, delete", file=sys.stderr)
