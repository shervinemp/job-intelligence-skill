"""diff — consecutive-run comparison (the regression canary).

Domain-free: works on any pair of dossiers with the standard shape
({fields: [{label, outcome}], summary: {filled}}). A field that WAS
filled and now is not = regression; the reverse = improvement.
"""
import json
import os


def load_handoffs(jid, results_dir):
    """Timestamped dossier history for a job, newest first."""
    d = os.path.join(results_dir, str(jid), "handoffs")
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        try:
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                out.append(json.load(fh))
        except Exception:
            continue
    return out


def compare_handoffs(new, old):
    """Field-level comparison of two dossiers.

    Returns dict: {"regressed": [(label, now_outcome)], "improved": [labels],
                   "still_failed": [labels], "filled_before": n, "filled_now": n}.
    regressed = was filled, now not — the canary for broken changes."""
    def index(h):
        return {f.get("label"): f for f in h.get("fields", [])}

    ni, oi = index(new), index(old)
    labels = set(ni) | set(oi)
    regressed, improved, still_failed = [], [], []
    for lbl in sorted(labels):
        nf, of = ni.get(lbl), oi.get(lbl)
        no = nf.get("outcome") if nf else None
        oo = of.get("outcome") if of else None
        if no == "filled" and oo != "filled":
            improved.append(lbl)
        elif no != "filled" and oo == "filled":
            regressed.append((lbl, no or "-"))
        elif no != "filled" and oo is not None and oo != "filled":
            still_failed.append(lbl)
    return {"regressed": regressed, "improved": improved,
            "still_failed": still_failed,
            "filled_before": old.get("summary", {}).get("filled"),
            "filled_now": new.get("summary", {}).get("filled")}
