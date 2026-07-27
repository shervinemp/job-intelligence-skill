"""registry_cli.py — `apply.py registry` subcommand.

Manages the adaptive probe layer's learned state:
  - observations (capability-keyed probe-routing hints)
  - corpus (captured DOM snapshots for offline testing)
  - failures (cascade-miss artifacts for debugging)

Usage:
    apply.py registry candidates            List unconfirmed observations
    apply.py registry confirm <hash>        Manually promote an observation
    apply.py registry clear <hash>          Delete an observation
    apply.py registry corpus                List captured DOM snapshots
    apply.py registry failures              List cascade-miss artifacts
"""
from __future__ import annotations

import sys
from pathlib import Path

from lib.config import JI_HOME


def _short(s, n=80):
    s = str(s or "")
    return s if len(s) <= n else s[:n - 1] + "..."


def cmd_registry(action: str, hash_key=None):
    if action == "candidates":
        _cmd_candidates()
    elif action == "confirm":
        if not hash_key:
            print("ERROR: confirm requires a profile hash", file=sys.stderr)
            return 1
        _cmd_confirm(hash_key)
    elif action == "clear":
        if not hash_key:
            print("ERROR: clear requires a profile hash", file=sys.stderr)
            return 1
        _cmd_clear(hash_key)
    elif action == "corpus":
        _cmd_corpus()
    elif action == "failures":
        _cmd_failures()
    return 0


def _cmd_candidates():
    """List all observations, marking which are confirmed vs pending."""
    from apply.common.observations import list_all, CONFIRM_THRESHOLD
    records = list_all()
    if not records:
        print("No observations yet — run `apply.py auto` to accumulate.", file=sys.stderr)
        return
    print(f"{'HASH':<10} {'STATUS':<10} {'WINS':>5} {'STRATEGY':<18} {'DOMAINS':<40}", file=sys.stderr)
    print("-" * 90, file=sys.stderr)
    for r in sorted(records, key=lambda x: (not x.get("confirmed"), -x.get("success_count", 0))):
        h = r.get("profile_hash", "")[:8]
        status = "CONFIRMED" if r.get("confirmed") else "pending"
        wins = r.get("success_count", 0)
        strategy = r.get("winning_strategy") or (r.get("candidate_strategies") or ["?"])[0]
        domains = ",".join((r.get("domain_examples") or [])[:3])
        print(f"{h:<10} {status:<10} {wins:>5} {strategy:<18} {_short(domains, 40)}", file=sys.stderr)
    confirmed = sum(1 for r in records if r.get("confirmed"))
    print(f"\n{confirmed} confirmed, {len(records) - confirmed} pending (need {CONFIRM_THRESHOLD} consistent wins)",
          file=sys.stderr)


def _cmd_confirm(hash_key):
    """Manually promote an observation to confirmed."""
    from apply.common.observations import confirm_hash
    # Allow partial hash match (first 8 chars)
    if len(hash_key) < 16:
        # Find the full hash starting with this prefix
        from apply.common.observations import list_all
        for r in list_all():
            if r["profile_hash"].startswith(hash_key):
                hash_key = r["profile_hash"]
                break
    if confirm_hash(hash_key):
        print(f"CONFIRMED: {hash_key[:8]}", file=sys.stderr)
    else:
        print(f"ERROR: no observation found for {hash_key[:8]} (or it has no winning_strategy)",
              file=sys.stderr)


def _cmd_clear(hash_key):
    """Delete an observation record."""
    from apply.common.observations import clear_hash
    if len(hash_key) < 16:
        from apply.common.observations import list_all
        for r in list_all():
            if r["profile_hash"].startswith(hash_key):
                hash_key = r["profile_hash"]
                break
    if clear_hash(hash_key):
        print(f"CLEARED: {hash_key[:8]}", file=sys.stderr)
    else:
        print(f"ERROR: no observation found for {hash_key[:8]}", file=sys.stderr)


def _cmd_corpus():
    """List captured DOM snapshots."""
    from apply.common.corpus import list_all
    entries = list_all()
    if not entries:
        print("No corpus snapshots yet — run `apply.py auto` to capture.",
              file=sys.stderr)
        return
    print(f"{'HASH':<10} {'PLATFORM':<14} {'STRATEGY':<18} {'SIZE':>8} {'URL':<40}", file=sys.stderr)
    print("-" * 95, file=sys.stderr)
    for e in sorted(entries, key=lambda x: x.get("captured_at", "")):
        h = e.get("profile_hash", "")[:8]
        platform = e.get("platform", "?")[:13]
        strategy = e.get("winning_strategy", "?")[:17]
        size = e.get("html_size", 0)
        size_str = f"{size // 1024}K" if size > 1024 else f"{size}B"
        url = _short(e.get("url", ""), 40)
        print(f"{h:<10} {platform:<14} {strategy:<18} {size_str:>8} {url}", file=sys.stderr)
    total = sum(e.get("html_size", 0) for e in entries)
    print(f"\n{len(entries)} snapshots, {total // 1024}KB total", file=sys.stderr)


def _cmd_failures():
    """List cascade-miss failure artifacts."""
    failures_dir = Path(JI_HOME) / "registry-failures"
    if not failures_dir.exists():
        print("No failure artifacts yet.", file=sys.stderr)
        return
    import json
    entries = []
    for json_path in sorted(failures_dir.glob("*_probe.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            data["_path"] = str(json_path)
            entries.append(data)
        except Exception:
            continue
    if not entries:
        print("No failure artifacts yet.", file=sys.stderr)
        return
    print(f"{'TIMESTAMP':<20} {'HASH':<10} {'JID':<22} {'CAPS':<40} {'URL':<40}", file=sys.stderr)
    print("-" * 130, file=sys.stderr)
    for e in entries[-25:]:
        ts = e.get("timestamp", "")[:19]
        h = e.get("profile_hash", "")[:8]
        jid = _short(e.get("jid", ""), 21)
        caps = _short(e.get("capability_summary", ""), 40)
        url = _short(e.get("url", ""), 40)
        print(f"{ts:<20} {h:<10} {jid:<22} {caps:<40} {url}", file=sys.stderr)
    print(f"\n{len(entries)} failure artifacts (showing last 25)", file=sys.stderr)
    print("View DOM with: apply.py act --inspect <jid>  or open the *_dom.html file in a browser",
          file=sys.stderr)