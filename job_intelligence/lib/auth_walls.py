"""Per-job tracking of auth-walled jobs in ~/.openclaw/needs_auth.json.

Operations are per-jid (not wholesale) so failed retries don't lose entries.
"""

import json
import os
from urllib.parse import urlparse

NEEDS_AUTH_PATH = os.path.join(
    os.path.expanduser("~"), ".openclaw", "needs_auth.json"
)


def _read():
    if not os.path.exists(NEEDS_AUTH_PATH):
        return []
    try:
        with open(NEEDS_AUTH_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _write(entries):
    os.makedirs(os.path.dirname(NEEDS_AUTH_PATH), exist_ok=True)
    with open(NEEDS_AUTH_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def add(jid, url, title, company):
    entries = _read()
    existing_jids = {e.get("jid") for e in entries}
    if jid in existing_jids:
        return
    domain = urlparse(url).netloc
    entries.append({
        "jid": jid, "url": url, "domain": domain,
        "title": title, "company": company,
    })
    _write(entries)


def remove(jid):
    entries = _read()
    before = len(entries)
    entries = [e for e in entries if e.get("jid") != jid]
    if len(entries) != before:
        _write(entries)


def list_all():
    return _read()


def count():
    return len(_read())


def domains():
    entries = _read()
    return sorted(set(e.get("domain", "?") for e in entries))
