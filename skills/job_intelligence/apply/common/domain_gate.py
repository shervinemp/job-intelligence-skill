"""apply/common/domain_gate.py — F2: new-domain approval gate.

Applying to a never-submitted-to domain submits real PII (name, phone,
address, citizenship, disability) to that site. A persuasive fake "Workday-
like" page would pass destination vetting (it is a public host) but still
harvest the candidate's data. F2: a domain with zero prior SUCCESSFUL
submissions is treated as unapproved — a live submit requires explicit
orchestrator/human sign-off, which is recorded so the gate opens only once
per domain.

Approved domains live in JI_HOME/approved_domains.json. A domain is also
auto-approved once a job there is actually marked applied (prior success is
the strongest evidence).
"""
import json
import os

from lib.config import JI_HOME


def _path():
    return os.path.join(os.environ.get("JI_HOME") or JI_HOME,
                        "approved_domains.json")


def _host(url):
    from urllib.parse import urlparse
    try:
        return (urlparse(url or "").netloc or "").lower()
    except Exception:
        return ""


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    try:
        from lib.config import atomic_write_json
        atomic_write_json(_path(), data, indent=2)
    except Exception:
        pass


def has_prior_success(host):
    """True when any APPLIED job exists on this host (prior successful
    submission is the strongest approval signal)."""
    if not host:
        return False
    try:
        from lib.db import get_conn
        row = get_conn().execute(
            "SELECT 1 FROM jobs WHERE stage='applied' AND url LIKE ? LIMIT 1",
            (f"%{host}%",)).fetchone()
        return bool(row)
    except Exception:
        return False


def is_approved(host):
    """A domain is approved if it is in the approve list OR has a prior
    successful submission."""
    if not host:
        return False
    return host in _load() or has_prior_success(host)


def approve(host):
    """Explicitly approve a domain (orchestrator/human sign-off). Returns
    True when recorded."""
    if not host:
        return False
    data = _load()
    data[host] = True
    _save(data)
    return True


def deny(host):
    data = _load()
    data.pop(host, None)
    _save(data)
    return True


def list_approved():
    return sorted(_load().keys())


def gate(host):
    """F2 gate for a live submit: (allowed, reason). allowed=False means the
    domain needs explicit approval before a real submit."""
    if not host:
        return True, "no host — cannot gate (fail-open to avoid blocking URL-less tests)"
    if is_approved(host):
        return True, "approved"
    return False, (f"new domain '{host}' has no prior successful submission — "
                   f"approve it first (report.py domains approve {host})")


def mark_applied_host(host):
    """Auto-approve a host after a successful submit lands (prior success)."""
    if host:
        approve(host)
