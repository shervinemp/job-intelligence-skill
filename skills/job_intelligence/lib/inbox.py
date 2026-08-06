"""lib/inbox.py — read the user's inbox for auth completion.

The auth flow (act/auth_flow.py) detects 2FA prompts and account-verification
walls and hands them to a human. This module gives it the OTHER half: read the
inbox (via gmail-cli, the same reader stage_emails.py uses), find the security
code or verification link the platform just sent, and hand it back for the
flow to complete automatically.

SAFETY (fail-closed extraction):
- A code is only extracted when a strong keyword (code/verification/one-time/
  otp/security) is near a standalone 4-8 digit number — no guessing digits
  out of prose.
- A verification link is only returned when its visible text says
  verify/confirm/activate AND its URL host matches the auth domain (or a
  known mail/verify host). We never click an arbitrary link from email — the
  email could be a fake sent to a look-alike address. When the evidence
  doesn't match, return None and the caller hands off to the human.
"""

import json
import os
import re
import subprocess
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GMAIL_CLI = os.path.join(SKILL_DIR, "..", "gmail-cli", "gmail_cli.py")

_CODE_KEYWORDS = re.compile(
    r"(?:verification|security|one[- ]time|otp|two[- ]?factor|2fa|login|confirm)"
    r"[^\d]{0,25}(?P<code>\b\d{4,8}\b)", re.IGNORECASE)
_CODE_KEYWORDS_REV = re.compile(
    r"(?P<code>\b\d{4,8}\b)[^\d]{0,25}"
    r"(?:code|verification|otp|security)", re.IGNORECASE)

_VERIFY_LINK_TEXT = re.compile(r"\b(verify|confirm|activate)\b", re.IGNORECASE)

# Hosts a verification link may legitimately live on even when the ATS domain
# differs (e.g. message-tracking or dedicated verify hosts).
_KNOWN_VERIFY_HOSTS = (
    "workday.com", "oraclecloud.com", "icims.com", "greenhouse.io",
    "lever.co", "smartrecruiters.com", "successfactors.com",
    "bamboohr.com", "ashbyhq.com", "myworkdayjobs.com",
)


def _registrable_domain(url):
    """Extract the registrable domain from a URL or bare hostname."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url or "").netloc.lower()
        if not host:
            # Bare hostname (no scheme) — urlparse puts it in path.
            host = (url or "").lower().split("/")[0].split(":")[0]
    except Exception:
        return ""
    host = host.split(":")[0].strip()
    parts = [p for p in host.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def search_mail(query, max_results=8, timeout=60):
    """Search the inbox via gmail-cli. Returns a list of
    {id, date, from, subject} dicts (newest first). Empty on failure."""
    try:
        r = subprocess.run(
            [sys.executable, GMAIL_CLI, "gmail", "search"] +
            query.split() + ["--all", "--json", "--max", str(max_results)],
            capture_output=True, timeout=timeout,
        )
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout.decode("utf-8", errors="replace"))
        out = []
        for t in data.get("threads", []):
            out.append({
                "id": t.get("id", ""),
                "date": t.get("date", ""),
                "from": t.get("from", ""),
                "subject": t.get("subject", ""),
            })
        return out
    except Exception:
        return []


def fetch_body(message_id, timeout=60):
    """Fetch a message body (cleaned text) via gmail-cli get."""
    try:
        r = subprocess.run(
            [sys.executable, GMAIL_CLI, "gmail", "get", message_id],
            capture_output=True, timeout=timeout,
        )
        if r.returncode != 0:
            return ""
        return r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def clean_text(raw):
    """Strip HTML/headers from a fetched message into searchable text."""
    if not raw:
        return ""
    # gmail-cli prints headers then a blank line then the body — split there.
    parts = raw.split("\n\n", 1)
    body = parts[1] if len(parts) > 1 else raw
    body = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", body,
                  flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"&nbsp;?", " ", body)
    body = re.sub(r"\s+", " ", body)
    return body.strip()


def extract_security_code(text):
    """Extract a security code from message text. Returns the digit string or
    None. Requires a strong keyword adjacent to a standalone 4-8 digit number
    — never a bare digit regex over prose."""
    t = clean_text(text)
    if not t:
        return None
    for pat in (_CODE_KEYWORDS, _CODE_KEYWORDS_REV):
        m = pat.search(t)
        if m:
            return m.group("code")
    return None


def extract_verification_link(text, auth_domain=""):
    """Find a verification/activation link in message text (HTML-aware).

    Returns the URL or None. The link must (a) be a real http(s) URL, (b) have
    visible text matching verify/confirm/activate, and (c) resolve to the auth
    domain OR a known verify host. Never return an unverifiable link — the
    caller must not click email links it cannot attribute. Link extraction
    runs on the RAW message (before HTML stripping) so <a href> pairs survive.
    """
    raw = text or ""
    if not raw:
        return None
    # Pull <a href="URL">LABEL</a> pairs from the raw (possibly HTML) body.
    for m in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                         raw, flags=re.DOTALL | re.IGNORECASE):
        url, label = m.group(1).strip(), m.group(2)
        label = re.sub(r"<[^>]+>", "", label or "")
        label = re.sub(r"\s+", " ", label).strip()
        if not _VERIFY_LINK_TEXT.search(label or ""):
            continue
        try:
            from urllib.parse import urlparse
            host = urlparse(url).netloc.lower().split(":")[0]
        except Exception:
            continue
        dom = _registrable_domain(host)
        if auth_domain and _registrable_domain(auth_domain) == dom:
            return url
        if any(dom == _registrable_domain(k) or dom.endswith("." + k)
               for k in _KNOWN_VERIFY_HOSTS):
            return url
    # Fallback: plain-text URLs with a trailing verify-ish label in prose.
    t = clean_text(raw)
    for m in re.finditer(r'(https?://[^\s<>"\'\[\]()]+)', t):
        url = m.group(1)
        ctx = t[max(0, m.start() - 60):m.end() + 60]
        if not _VERIFY_LINK_TEXT.search(ctx):
            continue
        try:
            from urllib.parse import urlparse
            host = urlparse(url).netloc.lower().split(":")[0]
        except Exception:
            continue
        dom = _registrable_domain(host)
        if auth_domain and _registrable_domain(auth_domain) == dom:
            return url
        if any(dom == _registrable_domain(k) or dom.endswith("." + k)
               for k in _KNOWN_VERIFY_HOSTS):
            return url
    return None


def find_security_email(auth_domain, kind="code", query_extra="", max_results=8):
    """Find the recent auth email for `auth_domain` in the inbox.

    kind="code"     → a security/verification code email.
    kind="verify"   → an account verification/activation email.

    Returns dict {id, from, subject, body, code|link} or None when nothing
    attributable to the domain matches.
    """
    dom = _registrable_domain(auth_domain)
    if not dom:
        return None
    base = f'from:{dom}'
    if query_extra:
        base = f"{base} {query_extra}"
    for msg in search_mail(base, max_results=max_results):
        subject = (msg.get("subject") or "")
        body = fetch_body(msg["id"])
        if kind == "code":
            code = extract_security_code(body) or extract_security_code(subject)
            if code:
                return {**msg, "body": body, "code": code}
        else:
            link = extract_verification_link(body, auth_domain)
            if link:
                return {**msg, "body": body, "link": link}
    return None
