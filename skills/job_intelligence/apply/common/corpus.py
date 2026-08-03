"""corpus.py — Platform page snapshots for offline testing.

Captures real DOM snapshots from every platform the probe encounters,
keyed by capability profile hash. Each snapshot is a complete HTML file
plus a JSON sidecar with the URL, platform name, capability profile,
and the winning probe strategy.

The corpus serves two purposes:

1. **Regression testing** — tests/mock_page.py loads these HTML files
   in jsdom (via node subprocess) and replays the real _SCAN_JS and
   _READER_JS against them. If a filler change breaks Workday dropdown
   detection, the test fails BEFORE a real job is affected.

2. **Platform archaeology** — `apply.py registry corpus` lists every
   captured platform and its profile hash, so we can see which
   capability shapes we've actually encountered.

Capture is automatic: on the first successful probe of a new profile
hash, the router saves the DOM. If a file already exists for that
hash, it's not overwritten (first-captured wins — that's the "known
good" snapshot). Manual re-capture via `apply.py registry corpus
recapture <hash>`.

File layout: ~/.ji/registry-corpus/<profile_hash>.html
             ~/.ji/registry-corpus/<profile_hash>.json
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

from lib.config import JI_HOME


def _corpus_dir() -> Path:
    return Path(JI_HOME) / "registry-corpus"


def _corpus_html_path(hash_key: str) -> Path:
    return _corpus_dir() / f"{hash_key}.html"


def _corpus_json_path(hash_key: str) -> Path:
    return _corpus_dir() / f"{hash_key}.json"


def capture_from_html(html: str, hash_key: str, profile: Optional[dict],
                      winning_strategy: str, platform_name: str = "",
                      jid: str = "", url: str = "") -> Optional[str]:
    """Capture from an existing HTML artifact (e.g. an inspect dump saved
    during a bug hunt) instead of a live page — the capture-on-fix
    workflow: fix a browser bug → snapshot the page → golden test
    protects the fix. Same first-wins + PII-scrub semantics as capture()."""
    if has_snapshot(hash_key):
        return None
    try:
        _corpus_dir().mkdir(parents=True, exist_ok=True)
        if not html or len(html) < 50:
            return None
        _corpus_html_path(hash_key).write_text(scrub_pii(html), encoding="utf-8")
        sidecar = {
            "profile_hash": hash_key,
            "url": url,
            "platform": platform_name,
            "strategy": winning_strategy,
            "jid": jid,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "via": "capture_from_html",
        }
        p = _corpus_json_path(hash_key)
        p.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        return str(p)
    except Exception:
        return None


def has_snapshot(hash_key: str) -> bool:
    """True if a snapshot already exists for this profile hash."""
    return _corpus_html_path(hash_key).exists()


_SCRUB_PATTERNS = [
    # emails
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    # phones (incl. +1 (343) 555-1234 variants)
    (re.compile(r"\+?\d[\d\s().-]{8,}\d"), "[phone]"),
]


def scrub_pii(html: str) -> str:
    """Redact PII from a DOM snapshot before storage — the corpus holds
    real form pages with real names/emails/phones; the red line applies
    to disk too."""
    for pat, repl in _SCRUB_PATTERNS:
        html = pat.sub(repl, html)
    return html


def capture(page, profile: Optional[dict], hash_key: str,
            winning_strategy: str, platform_name: str = "",
            jid: str = "") -> Optional[str]:
    """Save a DOM snapshot + metadata sidecar.

    Only captures if no snapshot exists for this hash (first-wins).
    Returns the path to the JSON sidecar, or None if capture failed
    or was skipped (already exists).
    """
    if has_snapshot(hash_key):
        return None
    try:
        _corpus_dir().mkdir(parents=True, exist_ok=True)
        # Save HTML (PII-scrubbed — the corpus is evidence, not a leak)
        try:
            html = page.evaluate("() => document.documentElement.outerHTML")
        except Exception:
            return None
        if not html or len(html) < 50:
            return None
        _corpus_html_path(hash_key).write_text(
            scrub_pii(html), encoding="utf-8")
        # Save sidecar
        sidecar = {
            "profile_hash": hash_key,
            "url": page.url,
            "platform": platform_name or "unknown",
            "jid": jid,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "winning_strategy": winning_strategy,
            "capability_profile": profile or {},
        }
        json_path = _corpus_json_path(hash_key)
        json_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        print(f"CORPUS_CAPTURE: {hash_key[:8]} platform={platform_name or '?'} "
              f"strategy={winning_strategy} url={page.url[:80]}", file=sys.stderr)
        return str(json_path)
    except Exception as e:
        print(f"CORPUS_CAPTURE_FAIL: {e}", file=sys.stderr)
        return None


def recapture(page, profile: Optional[dict], hash_key: str,
              winning_strategy: str, platform_name: str = "",
              jid: str = "") -> Optional[str]:
    """Force re-capture (overwrites existing snapshot)."""
    try:
        _corpus_html_path(hash_key).unlink(missing_ok=True)
    except Exception:
        pass
    # Temporarily bypass has_snapshot by calling capture after delete
    # — but capture checks has_snapshot, so we need to write directly.
    try:
        _corpus_dir().mkdir(parents=True, exist_ok=True)
        html = page.evaluate("() => document.documentElement.outerHTML")
        if not html or len(html) < 50:
            return None
        _corpus_html_path(hash_key).write_text(html, encoding="utf-8")
        sidecar = {
            "profile_hash": hash_key,
            "url": page.url,
            "platform": platform_name or "unknown",
            "jid": jid,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "winning_strategy": winning_strategy,
            "capability_profile": profile or {},
        }
        json_path = _corpus_json_path(hash_key)
        json_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        print(f"CORPUS_RECAPTURE: {hash_key[:8]}", file=sys.stderr)
        return str(json_path)
    except Exception as e:
        print(f"CORPUS_RECAPTURE_FAIL: {e}", file=sys.stderr)
        return None


def list_all() -> list[dict]:
    """List all corpus entries with metadata."""
    d = _corpus_dir()
    if not d.exists():
        return []
    out = []
    for json_path in sorted(d.glob("*.json")):
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
            data["html_exists"] = _corpus_html_path(data.get("profile_hash", "")).exists()
            data["html_size"] = _corpus_html_path(data.get("profile_hash", "")).stat().st_size if data.get("html_exists") else 0
            out.append(data)
        except Exception:
            continue
    return out


def load_html(hash_key: str) -> Optional[str]:
    """Load a saved HTML snapshot. Returns None if not found."""
    p = _corpus_html_path(hash_key)
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def load_sidecar(hash_key: str) -> Optional[dict]:
    """Load the metadata sidecar for a snapshot."""
    p = _corpus_json_path(hash_key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete_snapshot(hash_key: str) -> bool:
    """Delete a corpus entry."""
    deleted = False
    try:
        _corpus_html_path(hash_key).unlink(missing_ok=True)
        deleted = True
    except Exception:
        pass
    try:
        _corpus_json_path(hash_key).unlink(missing_ok=True)
        deleted = True
    except Exception:
        pass
    return deleted