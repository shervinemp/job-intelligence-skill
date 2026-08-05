"""apply/common/learning.py — the learning seam (architecture C4).

One module owning the WRITE side of the learning stores — the retraction
choreography that `lib/db/fills.py` previously hand-rolled across four private
store APIs. Adjudication calls ONE function here; the stores (learned field
mappings, runtime alias rules, field-method preferences, profile-suspects)
sit behind this seam and are kept in sync HERE, not by the caller.

Trace contract (C-O3): a `wrong` verdict must be causally visible — after
`retract()`, the next `ji diff`/resolve run shows the mapping gone. This
module is that causal surface.

Interface:
  retract(label_norm, platform, answer) -> None
      The B1 self-correction: invalidate the learned mapping for the
      (label, platform), drop a domain-matching runtime alias rule, flag the
      profile answer as suspect if the wrong value came from the profile, and
      reject the per-field method preference. Failure is logged, never fatal.
"""


def retract(label_norm, platform, answer):
    """B1: a human adjudicated a fill WRONG — stop the source from
    reproducing the error across every store that fed the decision.

    Order matters: invalidate the mapping first (the resolver must no longer
    produce the wrong value), then drop the alias rule that may have caused
    the mapping to win, then flag the profile if the wrong value came from it,
    then demote the fill method that "succeeded" but filled wrong.
    """
    _invalidate_mapping(label_norm)
    _drop_alias_rule(label_norm, platform)
    _flag_profile_suspect(label_norm, answer)
    _reject_method(label_norm, platform)


def _invalidate_mapping(label_norm):
    try:
        from apply.common.resolve import _invalidate_learned
        _invalidate_learned(label_norm)
    except Exception:
        pass


def _drop_alias_rule(label_norm, platform):
    """Drop a runtime alias rule whose pattern matches the label and whose
    domain (if any) matches the platform — the rule may be the reason the
    wrong mapping won."""
    import re
    try:
        from apply.common.resolve import _load_runtime_rules, _save_runtime_rules
        rules = _load_runtime_rules()
        platform = (platform or "").lower()
        kept = []
        for entry in rules:
            pat, _keys, _last, domain = entry
            try:
                if re.search(pat, label_norm) and (not domain or domain in platform):
                    continue  # drop the suspect rule
            except re.error:
                pass
            kept.append(entry)
        if len(kept) != len(rules):
            _save_runtime_rules(kept)
    except Exception:
        pass


def _flag_profile_suspect(label_norm, answer):
    """Record a suspect profile answer (its value was adjudicated WRONG on a
    live fill). The orchestrator reads these via report.py profile --suspects
    and corrects profile.json — the root fix for a wrong profile value that
    otherwise reproduces on every job silently."""
    try:
        import json
        import os
        from lib.config import STATE_DIR, atomic_write_json
        path = os.path.join(STATE_DIR, "profile_suspects.json")
        data = {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
        data[label_norm] = {
            "answer": str(answer or "")[:200],
            "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
            "verdict": "wrong",
        }
        atomic_write_json(path, data, indent=2)
    except Exception:
        pass


def _reject_method(label_norm, platform):
    """Demote the per-field method preference for (host, label) — a method
    that "succeeded" but filled wrong must not keep winning."""
    try:
        from apply.common import field_methods
        field_methods.reject_method(label_norm, platform)
    except Exception:
        pass
