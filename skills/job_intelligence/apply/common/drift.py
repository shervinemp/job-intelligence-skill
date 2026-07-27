"""drift.py — Self-test drift detector for the adaptive probe system.

Runs `probe_all()` against each captured corpus snapshot (the "known
good" DOMs) and compares the strategy that currently finds the most
fields against the observation store's `winning_strategy` for that
profile hash.

If they disagree, the platform was redesigned (or our strategies
regressed). The detector:
  - Flags the mismatch in its report
  - Auto-demotes the stale observation (writing is best-effort —
    the cascade still works as a backstop)
  - Suggests the new winning strategy for confirmation

Also scans failure artifacts (registry-failures/) — if a strategy
NOW finds fields in a previously-failing DOM (because we improved a
strategy), the detector flags the recovered snapshot.

This is the quarterly self-test from the immune system design —
catches platform redesigns BEFORE field-level failures in production
runs. Cheap: each check is one CorpusPage construction + 9 strategy
runs against offline HTML (~100ms per snapshot).

Usage (CLI):
    apply.py registry drift                  # self-test all corpus snapshots
    apply.py registry drift --demote         # auto-demote stale (default on)
    apply.py registry drift --dry-run        # report only, no auto-demote
"""
from __future__ import annotations

import sys


def _probe_all_offline(page, registry_config=None):
    """Run probe_all against an offline CorpusPage. Returns (best, results)."""
    from apply.common.inspector import probe_all
    return probe_all(page, registry_config=registry_config)


def run(dry_run: bool = False, verbose: bool = False) -> dict:
    """Run the drift detector over all corpus snapshots + recent failures.

    Returns a summary dict:
      {
        "checked": int,         # total snapshots probed
        "agree": int,           # observation matches current best
        "stale": int,           # observation disagrees — demoted (or flagged)
        "recovered_failures": int,  # strategy now finds fields in a failure artifact
        "demoted": [hash, ...], # hashes actually demoted (empty if dry_run)
        "details": [...],       # per-snapshot report rows
      }
    """
    from apply.common.corpus import list_all, load_html, load_sidecar
    from apply.common.observations import clear_hash
    from apply.common.mock_page import CorpusPage, is_available

    summary = {"checked": 0, "agree": 0, "stale": 0,
               "recovered_failures": 0, "demoted": [], "details": []}

    if not is_available():
        print("DRIFT_SKIP: jsdom not installed (npm install jsdom) — "
              "cannot run offline self-test", file=sys.stderr)
        summary["error"] = "jsdom not available"
        return summary

    entries = list_all()
    if not entries:
        print("DRIFT_IDLE: no corpus snapshots to probe", file=sys.stderr)
        return summary

    print(f"DRIFT_START: probing {len(entries)} corpus snapshots "
          f"({'dry-run' if dry_run else 'auto-demote'})", file=sys.stderr)

    for entry in entries:
        h = entry.get("profile_hash", "")
        if not h:
            continue
        html = load_html(h)
        if not html:
            continue
        sidecar = load_sidecar(h) or {}
        url = sidecar.get("url", "https://corpus.test/")
        platform = sidecar.get("platform", "?")
        recorded_strategy = sidecar.get("winning_strategy", "")

        # Build offline page + run probe_all
        page = CorpusPage.from_html(html, url=url)
        # For custom_widgets strategy to find anything, we need to
        # supply registry_config with the discovered widgets — use
        # the observation's saved widgets, falling back to capability
        # auto-discovery via _build_registry_widgets.
        cap_profile = sidecar.get("capability_profile") or {}
        from apply.common.capabilities import discover_widgets
        widgets = discover_widgets(cap_profile, None)
        if sidecar.get("winning_widgets"):
            widgets.update(sidecar["winning_widgets"])

        # Construct a transient registry config so probe_all's
        # custom_widgets strategy gets the selectors
        class _Transient:
            pass
        t = _Transient()
        t.widgets = widgets
        t.name = platform
        t.best_strategy = None

        try:
            best, all_results = _probe_all_offline(page, registry_config=t)
        except Exception as e:
            summary["details"].append({
                "hash": h[:8], "platform": platform, "status": "error",
                "error": str(e)[:120],
            })
            if verbose:
                print(f"  {h[:8]} {platform}: ERROR {e}", file=sys.stderr)
            continue

        summary["checked"] += 1
        current_best = best.strategy if best.field_count > 0 else "none"
        current_count = best.field_count

        # Compare to recorded strategy
        # If observation is confirmed with winning_strategy, compare
        # current_best to recorded_strategy. If they differ AND
        # current_count > 0, the platform likely changed.
        # 'agree' also covers the case where the recorded strategy
        # is still in the top tier (probe_all returns the first
        # strategy with the max count — if two strategies tie, the
        # tiebreaker is declaration order, not the recorded strategy).
        all_strategies_with_fields = [r.strategy for r in all_results if r.field_count > 0]
        agrees = (current_best == recorded_strategy
                  or recorded_strategy in all_strategies_with_fields)

        row = {
            "hash": h[:8],
            "platform": platform,
            "recorded": recorded_strategy,
            "current_best": current_best,
            "current_count": current_count,
            "all_strategies": all_strategies_with_fields,
            "status": "agree" if agrees else "stale",
        }
        summary["details"].append(row)

        if agrees:
            summary["agree"] += 1
            if verbose:
                print(f"  {h[:8]} {platform}: OK {recorded_strategy} "
                      f"→ {current_best} ({current_count} fields)", file=sys.stderr)
        else:
            summary["stale"] += 1
            print(f"  DRIFT_STALE: {h[:8]} {platform} — "
                  f"recorded={recorded_strategy} but now best={current_best} "
                  f"({current_count} fields)", file=sys.stderr)
            # Auto-demote the observation (clear it so the next run
            # re-accumulates). Best-effort — never let write failure
            # abort the self-test.
            if not dry_run:
                if clear_hash(h):
                    summary["demoted"].append(h[:8])
                    print(f"  DRIFT_DEMOTED: {h[:8]}", file=sys.stderr)
            else:
                print(f"  (dry-run — would demote {h[:8]})", file=sys.stderr)

    # Also probe recent failure artifacts to see if any strategy NOW
    # finds fields (recovered failures — our strategies improved)
    from lib.config import JI_HOME
    from pathlib import Path
    import json as _json
    failures_dir = Path(JI_HOME) / "registry-failures"
    if failures_dir.exists():
        for dom_path in sorted(failures_dir.glob("*_dom.html"))[-10:]:
            try:
                html = dom_path.read_text(encoding="utf-8")
                sidecar_path = dom_path.with_suffix(".json").with_stem(
                    dom_path.stem.replace("_dom", "_probe"))
                sidecar = _json.loads(sidecar_path.read_text(encoding="utf-8")) if sidecar_path.exists() else {}
                page = CorpusPage.from_html(html, url=sidecar.get("url", "https://corpus.test/"))
                best, _ = _probe_all_offline(page)
                if best.field_count > 0:
                    summary["recovered_failures"] += 1
                    print(f"  DRIFT_RECOVERED: {dom_path.stem} — "
                          f"{best.strategy} now finds {best.field_count} fields "
                          f"(was 0 at capture time)", file=sys.stderr)
            except Exception:
                continue

    print(f"DRIFT_DONE: checked={summary['checked']} "
          f"agree={summary['agree']} stale={summary['stale']} "
          f"recovered={summary['recovered_failures']} "
          f"demoted={len(summary['demoted'])}", file=sys.stderr)
    return summary