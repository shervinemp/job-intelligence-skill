"""lib/url_safety.py — trust boundary for URLs that came from outside.

Job URLs are harvested by regex from EMAIL BODIES (extract.py). Anyone who
can email the user can therefore choose a string that this pipeline will
later fetch. Two things then happen to it:

  1. `curl -L` follows it, historically with no protocol or redirect limit;
  2. Playwright navigates it in the pipeline's persistent Chrome profile —
     the one holding live LinkedIn and ATS session cookies.

So an untrusted string reached an authenticated browser. The old defence
was `_SKIP_DOMAINS`, a 20-entry substring DENYLIST aimed at newsletter
noise, not at attackers — it never looked at the scheme, and never looked
at where a host resolves.

This module is the allowlist-shaped replacement:

  * scheme must be http/https — no file:, javascript:, data:, ftp:;
  * the host must not resolve to a loopback, private, link-local,
    multicast or reserved address (blocks http://127.0.0.1:9222 — the
    pipeline's own CDP port — and cloud metadata endpoints);
  * userinfo (user:pass@host) is rejected: it is almost always an attempt
    to make a hostile host look like a familiar one.

DNS resolution is what makes this real rather than cosmetic: a name that
looks public can still resolve to 127.0.0.1. Resolution failures are
treated as UNSAFE — we do not fetch what we cannot vet.
"""
import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")

# Hosts the pipeline is allowed to open in the AUTHENTICATED browser
# profile. Everything else gets an ephemeral, cookie-less context.
_SESSION_HOST_SUFFIXES = (
    "linkedin.com",
    "myworkdayjobs.com", "myworkdaysite.com",
    "greenhouse.io", "boards.greenhouse.io",
    "lever.co", "jobs.lever.co",
    "ashbyhq.com", "jobs.ashbyhq.com",
    "icims.com", "bamboohr.com", "jobvite.com", "comeet.co",
    "smartrecruiters.com", "workable.com", "breezy.hr",
    "successfactors.com", "taleo.net", "adp.com",
)


def _resolved_addresses(host):
    infos = socket.getaddrinfo(host, None)
    return {ai[4][0] for ai in infos}


def is_safe_url(url, resolve=True):
    """(ok, reason). `reason` is empty when ok is True.

    resolve=False skips DNS (unit tests, offline) but still enforces the
    scheme/userinfo/literal-IP rules.
    """
    if not url or not isinstance(url, str):
        return False, "empty url"
    try:
        p = urlparse(url.strip())
    except ValueError as e:
        return False, f"unparseable url ({e})"

    if p.scheme.lower() not in ALLOWED_SCHEMES:
        return False, f"scheme '{p.scheme or '(none)'}' not allowed"
    if p.username or p.password:
        return False, "url embeds credentials (userinfo)"
    host = p.hostname
    if not host:
        return False, "no host"

    # A literal IP can be checked without DNS.
    try:
        ip = ipaddress.ip_address(host)
        return _vet_ip(ip, host)
    except ValueError:
        pass

    if not resolve:
        return True, ""
    try:
        addrs = _resolved_addresses(host)
    except (socket.gaierror, UnicodeError, OSError) as e:
        # Cannot vet it -> do not fetch it.
        return False, f"host does not resolve ({type(e).__name__})"
    if not addrs:
        return False, "host resolves to nothing"
    for a in addrs:
        try:
            ok, reason = _vet_ip(ipaddress.ip_address(a), host)
        except ValueError:
            return False, f"unparseable resolved address {a!r}"
        if not ok:
            return False, reason
    return True, ""


def _vet_ip(ip, host):
    # Order matters for the REASON only (all of these refuse): link-local
    # is also reported as private by ipaddress, and "link-local /
    # metadata endpoint" is the diagnosis an operator can act on.
    if ip.is_loopback:
        return False, f"{host} resolves to loopback {ip}"
    if ip.is_link_local:
        return False, f"{host} resolves to link-local {ip} (metadata endpoint)"
    if ip.is_private:
        return False, f"{host} resolves to private address {ip}"
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False, f"{host} resolves to reserved address {ip}"
    return True, ""


def allows_session_profile(url):
    """True when this URL may be opened in the COOKIE-BEARING Chrome
    profile. Untrusted hosts still get fetched — just not with the user's
    LinkedIn/ATS sessions attached."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host == s or host.endswith("." + s) for s in _SESSION_HOST_SUFFIXES)
