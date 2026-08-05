"""lib/db/fills.py — the fill ledger and its adjudication.

Why this exists (ETHOS §10). The pipeline's designated falsification
instrument is wrong-fill rate, and it was never computed. It could not
be: `kind=verified` asserts "the value I intended is present", and the
verifier re-scores with the SAME scorer that picked the value (match.py
is explicit that the two "can never disagree"), so verification is
tautological for semantic error. The probe router's success predicate is
`field_count > 0`. Every loop measured completion.

The ledger separates the two questions that were conflated:

    did the value land?   -> kind (verified / unverified / ...)  — mechanical
    was the value right?  -> verdict (correct / wrong / ...)     — adjudicated

Recording is passive: nothing here changes what the pipeline fills. It
only creates the denominator that makes the precision claim falsifiable.

Sampling for adjudication is deliberately biased toward the fills most
likely to be wrong — unverified reads, and fields whose read-back text
diverges from the intended answer — because a uniform sample of 845
fields spends a human's attention on the boring 90%.
"""

from .schema import get_conn


def _norm_label(s):
    import re
    return re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).strip()


def _platform_of(url):
    from urllib.parse import urlparse
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return ""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def record_fills(job_id, fields, run_id="", url=""):
    """Append one row per field decision. Returns the number written.

    `fields` is the dossier field list (label/answer/kind/method/reason/
    required/selected_text) — the same structure the orchestrator reads,
    so the ledger can never disagree with the dossier about what happened.
    """
    if not fields:
        return 0
    c = get_conn()
    platform = _platform_of(url)
    rows = []
    for f in fields:
        rows.append((
            job_id, run_id or "", platform,
            str(f.get("label", ""))[:300],
            _norm_label(f.get("label", ""))[:300],
            str(f.get("answer", ""))[:500],
            str(f.get("selected_text", ""))[:500],
            str(f.get("kind", "")),
            str(f.get("method", "")),
            str(f.get("reason", "")),
            1 if f.get("required") else 0,
        ))
    c.executemany(
        "INSERT INTO field_fills (job_id, run_id, platform, label, label_norm, "
        "answer, selected_text, kind, method, reason, required) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    return len(rows)


def sample_for_adjudication(limit=20, platform=None, only_unjudged=True):
    """Fills most worth a human verdict, riskiest first.

    Priority: unverified reads, then a read-back that differs from the
    intended answer, then everything else. Within a tier, spread across
    label_norm so one noisy field can't consume the whole sample.
    """
    c = get_conn()
    clauses = ["kind != ''"]
    params = []
    if only_unjudged:
        clauses.append("verdict IS NULL")
    if platform:
        clauses.append("platform = ?")
        params.append(platform)
    where = " AND ".join(clauses)
    rows = c.execute(
        f"""SELECT *,
              CASE
                WHEN kind = 'unverified' THEN 0
                WHEN selected_text != '' AND lower(selected_text) != lower(answer) THEN 1
                WHEN kind = 'verified' THEN 2
                ELSE 3
              END AS risk
            FROM field_fills WHERE {where}
            ORDER BY risk ASC, created_at DESC LIMIT ?""",
        params + [limit * 4],
    ).fetchall()
    out, seen = [], {}
    for r in rows:
        key = r["label_norm"]
        if seen.get(key, 0) >= 2:      # at most 2 of any one label
            continue
        seen[key] = seen.get(key, 0) + 1
        out.append(dict(r))
        if len(out) >= limit:
            break
    return out


def adjudicate(fill_id, verdict, note=""):
    """Record a correctness verdict. Returns True when a row changed.

    B1 self-correction: a `wrong` verdict RETRACTS the learned mapping for
    that (label, platform) and drops a domain-matching runtime alias rule —
    the falsification loop is closed: a human says "wrong", the source stops
    reproducing the error. `correct`/`unanswerable` leave learning alone.
    """
    if verdict not in ("correct", "wrong", "unanswerable"):
        raise ValueError(
            f"verdict must be correct|wrong|unanswerable, got {verdict!r}")
    c = get_conn()
    cur = c.execute(
        "UPDATE field_fills SET verdict=?, verdict_note=?, "
        "adjudicated_at=datetime('now') WHERE id=?",
        (verdict, str(note or "")[:300], fill_id))
    if cur.rowcount and verdict == "wrong":
        _retract_learning_for(c, fill_id)
    c.commit()
    return cur.rowcount > 0


def _retract_learning_for(c, fill_id):
    """On a `wrong` verdict: invalidate the learned mapping for this fill's
    (label, platform) and drop a runtime alias rule whose pattern matches the
    label (B1). If the wrong value came from the PROFILE (its value equals a
    profile answer), flag that answer as suspect (#3) — the source must be
    corrected, not just the label store. Failure to retract is logged, never
    fatal."""
    try:
        row = c.execute(
            "SELECT label, label_norm, platform, answer FROM field_fills "
            "WHERE id=?", (fill_id,)).fetchone()
        if not row:
            return
        label_norm = row["label_norm"]
        # 1. Retract the learned mapping (any domain-matching entry).
        from apply.common.resolve import _invalidate_learned
        _invalidate_learned(label_norm)
        # 2. Drop a runtime alias rule whose pattern matches this label.
        from apply.common.resolve import (_load_runtime_rules,
                                          _save_runtime_rules)
        rules = _load_runtime_rules()
        platform = (row["platform"] or "").lower()
        kept = []
        for entry in rules:
            pat, _keys, _last, domain = entry
            try:
                import re
                if re.search(pat, label_norm) and (not domain
                                                   or domain in platform):
                    continue  # drop the suspect rule
            except re.error:
                pass
            kept.append(entry)
        if len(kept) != len(rules):
            _save_runtime_rules(kept)
        # 3. Profile-source flag (#3): if the WRONG answer equals a profile
        #    answer, the profile itself is the poison source.
        try:
            _flag_profile_answer(label_norm, row["answer"])
        except Exception:
            pass
        # 4. Reject the per-field method preference for (host, label) — a
        #    method that "succeeded" but filled wrong must not keep winning.
        try:
            from apply.common import field_methods
            field_methods.reject_method(label_norm, platform)
        except Exception:
            pass
    except Exception:
        pass


def _flag_profile_answer(label_norm, answer):
    """Record a suspect profile answer (its value was adjudicated WRONG on a
    live fill). The orchestrator reads these via report.py profile --suspects
    and corrects profile.json — the root fix for a wrong profile value that
    otherwise reproduces on every job silently."""
    import json, os
    from lib.config import STATE_DIR
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
    try:
        from lib.config import atomic_write_json
        atomic_write_json(path, data, indent=2)
    except Exception:
        pass


def wrongfill_stats(platform=None):
    """Wrong-fill rate per platform and per field class.

    Returns {"overall": {...}, "by_platform": [...], "by_label": [...]}.
    `rate` is wrong / (correct + wrong) — 'unanswerable' is excluded from
    the denominator because it is a question about the FORM, not about
    whether the pipeline chose correctly.
    """
    c = get_conn()
    where = "WHERE verdict IN ('correct','wrong')"
    params = []
    if platform:
        where += " AND platform = ?"
        params.append(platform)

    def _bucket(group_col):
        rows = c.execute(
            f"""SELECT {group_col} AS k,
                   SUM(verdict='wrong')   AS wrong,
                   SUM(verdict='correct') AS correct
                FROM field_fills {where}
                GROUP BY {group_col} ORDER BY wrong DESC, correct DESC""",
            params).fetchall()
        out = []
        for r in rows:
            n = (r["wrong"] or 0) + (r["correct"] or 0)
            out.append({"key": r["k"] or "(none)", "wrong": r["wrong"] or 0,
                        "n": n, "rate": (r["wrong"] or 0) / n if n else None})
        return out

    tot = c.execute(
        f"""SELECT SUM(verdict='wrong') AS wrong, SUM(verdict='correct') AS correct
            FROM field_fills {where}""", params).fetchone()
    wrong, correct = (tot["wrong"] or 0), (tot["correct"] or 0)
    n = wrong + correct
    pending = c.execute(
        "SELECT COUNT(*) AS c FROM field_fills WHERE verdict IS NULL").fetchone()["c"]
    return {
        "overall": {"wrong": wrong, "n": n,
                    "rate": (wrong / n) if n else None, "pending": pending},
        "by_platform": _bucket("platform"),
        "by_label": _bucket("label_norm")[:15],
    }


# B2 — wrong-fill SPC tripwire. A platform whose adjudicated wrong-fill rate
# exceeds this bound, on a sample of at least MIN_N, is auto-paused (its
# autonomous submits are suppressed) until a human reviews. Center line +
# bound modeled as a crude control chart; the bound is deliberately
# conservative so a systematic error trips before it compounds.
SPC_MAX_RATE = 0.25
SPC_MIN_N = 5


def spc_trip(apply=True):
    """Evaluate the wrong-fill control chart. Returns the list of platforms
    that tripped (rate > SPC_MAX_RATE on ≥ SPC_MIN_N adjudicated fills).
    When `apply`, also writes `paused_platforms` into apply_policy.json so
    submits_for_real() suppresses them."""
    stats = wrongfill_stats()
    tripped = []
    for b in stats["by_platform"]:
        if b["n"] >= SPC_MIN_N and b["rate"] is not None \
                and b["rate"] > SPC_MAX_RATE:
            tripped.append(b["key"])
    if apply and tripped:
        try:
            import os
            from lib.config import JI_HOME
            pol_path = os.path.join(
                os.environ.get("JI_HOME") or JI_HOME, "apply_policy.json")
            pol = {}
            try:
                import json as _json
                with open(pol_path, encoding="utf-8") as f:
                    pol = _json.load(f)
            except Exception:
                pass
            if not isinstance(pol, dict):
                pol = {}
            existing = set(pol.get("paused_platforms") or [])
            existing.update(tripped)
            pol["paused_platforms"] = sorted(existing)
            from lib.config import atomic_write_json
            atomic_write_json(pol_path, pol, indent=2)
        except Exception:
            pass
    return tripped


def unpause_platform(platform):
    """Human override: remove a platform from the SPC pause set."""
    try:
        import os
        from lib.config import JI_HOME
        pol_path = os.path.join(
            os.environ.get("JI_HOME") or JI_HOME, "apply_policy.json")
        import json as _json
        with open(pol_path, encoding="utf-8") as f:
            pol = _json.load(f)
        if not isinstance(pol, dict):
            return False
        paused = set(pol.get("paused_platforms") or [])
        paused.discard(platform)
        pol["paused_platforms"] = sorted(paused)
        from lib.config import atomic_write_json
        atomic_write_json(pol_path, pol, indent=2)
        return True
    except Exception:
        return False


def correction_clusters(min_wrong=2):
    """D6 — correction root-cause clustering.

    Group `wrong` adjudicated fills by (label_norm, platform, method) and
    propose a root cause for each cluster that has ≥ min_wrong wrong verdicts.
    Upgrades adjudication from "retract" to "diagnose": a cluster's method
    points at the fix — combobox/select methods suggest a widget/option
    problem, text methods suggest a resolver/answer problem, a single
    platform+label cluster suggests a per-platform rule fix.

    Returns a list of dicts:
        {"label", "platform", "method", "wrong", "n", "root_cause",
         "fix"}
    """
    c = get_conn()
    rows = c.execute(
        """SELECT label_norm AS label, platform, method,
                  SUM(verdict='wrong') AS wrong,
                  COUNT(*) AS n
           FROM field_fills
           WHERE verdict IN ('correct','wrong')
           GROUP BY label_norm, platform, method
           ORDER BY wrong DESC""").fetchall()
    out = []
    for r in rows:
        wrong = r["wrong"] or 0
        if wrong < min_wrong:
            continue
        method = (r["method"] or "").lower()
        if method in ("combobox", "select", "dropdown", "radio"):
            root, fix = ("widget/option mismatch",
                         "check the option matcher / add a per-platform "
                         "country-picker verification rule")
        elif method in ("text", "text_fallback", "native_setter"):
            root, fix = ("resolver / answer value",
                         "add or fix an alias rule for this label, or update "
                         "the profile answer")
        else:
            root, fix = ("unknown", "inspect the adjudicated fills")
        out.append({"label": r["label"], "platform": r["platform"],
                    "method": r["method"], "wrong": wrong, "n": r["n"],
                    "root_cause": root, "fix": fix})
    return out
