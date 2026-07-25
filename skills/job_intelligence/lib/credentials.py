"""lib/credentials.py — Credential vault for job application sites.

Primary: OS keychain (Windows Credential Manager via keyring).
Fallback: plaintext JSON in ~/.ji/credentials.json (gitignored, user-owned).

Per-site credentials keyed by domain:
  get_creds("autodesk.wd1.myworkdayjobs.com") -> {"email": ..., "password": ...}

Account creation defaults sourced from profile.json.
"""
import json
import os
import secrets
import string
import sys

_SERVICE = "job-intelligence"

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


def get_creds(domain):
    """Return {"email": ..., "password": ...} or None."""
    domain = domain.lower()
    try:
        import keyring
        raw = keyring.get_password(_SERVICE, domain)
        if raw:
            data = json.loads(raw)
            if data.get("email") and data.get("password"):
                return data
    except Exception:
        pass
    fb = _fallback_read()
    entry = fb.get(domain)
    if entry and entry.get("email") and entry.get("password"):
        return entry
    return None


def save_creds(domain, email, password):
    domain = domain.lower()
    try:
        import keyring
        keyring.set_password(_SERVICE, domain, json.dumps({"email": email, "password": password}))
        return
    except Exception:
        pass
    fb = _fallback_read()
    fb[domain] = {"email": email, "password": password}
    _fallback_write(fb)


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


def gen_password(length=16):
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def cmd_creds(args):
    if not args:
        print("Usage: python apply.py creds <list|get|set|delete> [domain]", file=sys.stderr)
        return
    cmd = args[0]
    if cmd == "list":
        fb = _fallback_read()
        if not fb:
            print("(no fallback credentials — keyring-only entries not listed)", file=sys.stderr)
        for domain in sorted(fb):
            print(f"  {domain}: {fb[domain].get('email', '?')}")
        return
    if cmd == "get":
        if len(args) < 2:
            print("Usage: creds get <domain>", file=sys.stderr)
            return
        c = get_creds(args[1])
        if c:
            print(f"  email: {c['email']}")
            print(f"  password: {c['password']}")
        else:
            print("  (not found)", file=sys.stderr)
        return
    if cmd == "set":
        if len(args) < 4:
            print("Usage: creds set <domain> <email> <password>", file=sys.stderr)
            return
        save_creds(args[1], args[2], args[3])
        print(f"  saved {args[1]}", file=sys.stderr)
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
