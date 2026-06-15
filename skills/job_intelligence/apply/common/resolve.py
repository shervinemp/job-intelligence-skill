"""resolve.py — Label→value resolution chain with two-encounter rule and decisions.md context.

No answer values hardcoded in Python. All facts, derivations, and static answers
live in profile.json. decisions.md is a plain markdown the user edits.

Resolution order (strict):
  1. session_cache      (LLM guesses from current run, ephemeral)
  2. label_map          (persistent cache from prior confirmations)
  3. prefix match       (handles 60-char field_reader truncation)
  4. ephemeral exact    (profile facts + derivations + answers dict)
  5. --answers          (user override for this run)
  6. LLM selection      (selects among existing keys OR suggests new:key|value from .md)
"""
from __future__ import annotations

import json, os, re, time
from typing import Optional

# ─── Paths (anchored to profile.json directory) ──────────────────────

_JI_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LABEL_MAP_PATH = os.path.join(
    os.environ.get("JI_HOME", os.path.expanduser("~/.ji")), "label_map.json"
)
_SESSION_CACHE_PATH = os.path.join(
    os.environ.get("JI_HOME", os.path.expanduser("~/.ji")), "session_cache.json"
)
_DECISIONS_PATH = os.path.join(_JI_DIR, "decisions.md")


# ─── Resolution result ───────────────────────────────────────────────

class Resolution:
    __slots__ = ("value", "key", "label", "provenance", "ephemeral_only")
    def __init__(self, value, key, label, provenance, ephemeral_only):
        self.value = value
        self.key = key
        self.label = label
        self.provenance = provenance
        self.ephemeral_only = ephemeral_only


# ─── Normalization ───────────────────────────────────────────────────

def normalize(label: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", (label or "").lower()).strip()


# ─── Load / Save helpers ─────────────────────────────────────────────

def _load_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _load_decisions_md() -> str:
    try:
        with open(_DECISIONS_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return ""


# ─── Ephemeral answer builder ───────────────────────────────────────

_PROFILE_KEYS = {
    "first_name", "last_name", "email", "phone",
    "linkedin_url", "github_url", "portfolio_url",
    "resume_path", "location",
}

def _build_ephemeral(profile: dict) -> dict:
    ephemeral = {}

    for k in _PROFILE_KEYS:
        v = profile.get(k)
        if v:
            ephemeral[k] = (v, "profile")

    fn, ln = profile.get("first_name", ""), profile.get("last_name", "")
    if fn and ln:
        ephemeral["full_name"] = (f"{fn} {ln}", "derived")
    elif fn or ln:
        ephemeral["full_name"] = (fn or ln, "derived")

    loc = profile.get("location", "")
    if loc and "," in loc:
        parts = [p.strip() for p in loc.split(",")]
        if len(parts) >= 1 and parts[0]:
            ephemeral["city"] = (parts[0], "derived")
        if len(parts) >= 2 and parts[1]:
            ephemeral["state_province"] = (parts[1], "derived")
        if len(parts) >= 3 and parts[-1]:
            ephemeral["country"] = (parts[-1], "derived")

    answers = profile.get("answers", {})
    if isinstance(answers, dict):
        for k, v in answers.items():
            if v:
                ephemeral[k] = (v, "static")

    return ephemeral


def _find_ephemeral_value(key: str, ephemeral: dict) -> Optional[str]:
    entry = ephemeral.get(key)
    return entry[0] if entry else None


# ─── Resolution chain ────────────────────────────────────────────────

def resolve(
    label: str,
    profile: dict,
    session_cache: Optional[dict] = None,
    label_map: Optional[dict] = None,
    answers_override: Optional[dict] = None,
) -> Resolution:
    if session_cache is None:
        session_cache = {}
    if label_map is None:
        label_map = {}
    if answers_override is None:
        answers_override = {}

    norm = normalize(label)
    if not norm:
        return Resolution(None, None, label, "no_match", False)

    nf = lambda s: normalize(s)
    ephemeral = _build_ephemeral(profile)

    # Step 1: session_cache
    sc = session_cache.get(norm)
    if sc:
        val = _find_ephemeral_value(sc["key"], ephemeral)
        if val is not None:
            return Resolution(val, sc["key"], label, "session_cache", False)

    # Step 2: label_map
    lm = label_map.get(norm)
    if lm:
        val = _find_ephemeral_value(lm["key"], ephemeral)
        if val is not None:
            lm["hit_count"] = lm.get("hit_count", 0) + 1
            return Resolution(val, lm["key"], label, "label_map", False)
        label_map.pop(norm, None)

    # Step 3: prefix match (field_reader truncation)
    for cached_norm, entry in dict(session_cache, **label_map).items():
        if norm.startswith(cached_norm):
            val = _find_ephemeral_value(entry.get("key", ""), ephemeral)
            if val is not None:
                return Resolution(val, entry["key"], label, "label_map", False)

    # Step 4: ephemeral exact match (deterministic, no persistence needed)
    for key, (val, _source) in ephemeral.items():
        if nf(key.replace("_", " ")) == norm:
            return Resolution(val, key, label, "ephemeral", False)

    # Step 5: --answers override
    for k, v in answers_override.items():
        if nf(k) == norm:
            return Resolution(v, "answers_override", label, "user_typed", False)

    # Step 6: LLM selection (with decisions.md context)
    keys_list = list(ephemeral.keys())
    result = _llm_select(label, keys_list)
    if result:
        if result.startswith("new:"):
            parts = result[4:].split("|", 1)
            if len(parts) == 2:
                new_key, new_val = parts[0].strip(), parts[1].strip()
                if new_key and new_val:
                    ephemeral[new_key] = (new_val, "md_derived")
                    label_map[norm] = {"key": new_key, "provenance": "md_derived",
                                       "created": time.strftime("%Y-%m-%d"), "hit_count": 1}
                    _save_json(_LABEL_MAP_PATH, label_map)
                    p = _load_json(os.path.join(_JI_DIR, "profile.json"))
                    p.setdefault("answers", {})[new_key] = new_val
                    _save_json(os.path.join(_JI_DIR, "profile.json"), p)
                    return Resolution(new_val, new_key, label, "md_derived", False)

        elif result in ephemeral:
            val, source = ephemeral[result]
            enc_count = 1
            prev = None
            existing = session_cache.get(norm)
            if existing:
                prev = existing.get("key")
                if prev == result:
                    enc_count = existing.get("encounter_count", 1) + 1
                else:
                    enc_count = 1
            session_cache[norm] = {"key": result, "value": val,
                                   "encounter_count": enc_count, "previous_result": prev}
            _save_json(_SESSION_CACHE_PATH, session_cache)
            return Resolution(val, result, label, "llm_selected", enc_count < 2)

    return Resolution(None, None, label, "no_match", False)


# ─── LLM selection ──────────────────────────────────────────────────

def _llm_select(label: str, available_keys: list) -> Optional[str]:
    if not available_keys:
        return None
    md = _load_decisions_md()

    prompt = (
        f"Available answer keys: {json.dumps(available_keys)}\n"
        f"Form label: \"{label}\"\n"
    )
    if md:
        prompt += f"\nUser's rules (decisions.md):\n{md[:2000]}\n"

    prompt += (
        "\nReturn one of:\n"
        "- A key name from the above list → fill with that key's value\n"
        "- \"new:key_name|value\" → suggest a new answer derived from decisions.md\n"
        "- \"no_match\" → answer not found anywhere\n\n"
        "If the existing key's value is stale (decisions.md rules changed), "
        "use \"new:\" to suggest the correct new value.\n"
        "NEVER make up a value. Base it only on decisions.md."
    )

    try:
        from lib.ask_api import _text, _load_config
        reply, err = _text(prompt, 0.1, 64, _load_config())
        if err or not reply:
            return None
        result = (reply or "").strip().strip('"').strip("'")
        if result in available_keys:
            return result
        if result.startswith("new:") and "|" in result[4:]:
            return result
    except Exception:
        pass
    return None


# ─── Post-verify promotion ──────────────────────────────────────────

def promote_session_cache() -> int:
    """Promote session_cache entries that passed the two-encounter rule to label_map.
    Called after verify confirms submission. Returns count of new entries."""
    sc = _load_json(_SESSION_CACHE_PATH, {})
    if not sc:
        return 0
    lm = _load_json(_LABEL_MAP_PATH, {})
    now = time.strftime("%Y-%m-%d")
    promoted = 0

    for norm, entry in dict(sc).items():
        if entry.get("encounter_count", 1) >= 2 and norm not in lm:
            lm[norm] = {
                "key": entry["key"],
                "provenance": "encountered_twice",
                "created": now,
                "hit_count": 1,
            }
            sc.pop(norm, None)
            promoted += 1

    if promoted:
        _save_json(_LABEL_MAP_PATH, lm)
        _save_json(_SESSION_CACHE_PATH, sc)

    return promoted


# ─── Entry point for act.py ─────────────────────────────────────────

def resolution_for_fill(
    label: str,
    profile: dict,
    answers_override: Optional[dict] = None,
) -> Resolution:
    label_map = _load_json(_LABEL_MAP_PATH, {})
    session_cache = _load_json(_SESSION_CACHE_PATH, {})
    return resolve(label, profile, session_cache=session_cache,
                   label_map=label_map, answers_override=answers_override)
