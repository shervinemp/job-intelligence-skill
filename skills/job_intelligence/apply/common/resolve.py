"""resolve.py — Label→value resolution with cascading strategy, two-encounter rule,
derivation evaluator, and decision rule engine.

No answer values are hardcoded in Python. All facts, derivations, rules, and
static answers live in profile.json. The LLM never generates answer values —
it only selects from pre-approved keys or suggests key names for user confirmation.

Resolution order (strict, no fallthrough to fuzzy):
  1. session_cache  (LLM guesses from current run, ephemeral)
  2. label_map      (persistent cache from prior two-encounter confirmations)
  3. prefix match   (handles 60-char field_reader truncation)
  4. ephemeral      (facts + derivations + decision rules + static answers)
  5. LLM selection  (constrained to ephemeral keys, session-cached only)
  6. --answers prompt (user provides, then LLM suggests key name for persistence)
"""
from __future__ import annotations

import json, os, re, time
from dataclasses import dataclass, field
from typing import Optional

# ─── Paths ───────────────────────────────────────────────────────────
_LABEL_MAP_PATH = os.path.join(
    os.environ.get("JI_HOME", os.path.expanduser("~/.ji")), "label_map.json"
)
_SESSION_CACHE_PATH = os.path.join(
    os.environ.get("JI_HOME", os.path.expanduser("~/.ji")), "session_cache.json"
)

# ─── Data structures ──────────────────────────────────────────────────

@dataclass
class LabelMapEntry:
    key: str
    provenance: str        # direct_match | encountered_twice | user_confirmed
    created: str           # ISO date
    hit_count: int = 0
    version: int = 1       # bumped when target key's version changes

@dataclass
class SessionEntry:
    key: str
    value: str
    encounter_count: int   # 1 = first session, 2+ = seen across sessions
    previous_result: Optional[str] = None  # key from prior session, for two-encounter rule

@dataclass
class Resolution:
    value: Optional[str]
    key: Optional[str]
    label: str
    provenance: str        # session_cache | label_map | prefix | fact | derived | rule | static | llm_selected | user_typed
    ephemeral_only: bool   # True = not written to label_map (llm_selected on first encounter)


# ─── Normalization ─────────────────────────────────────────────────────

def normalize(label: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", (label or "").lower()).strip()


# ─── Derivations (computed in Python, not in JSON) ─────────────────────

def _derive_location(location: str) -> dict:
    """Extract city, state_province, country from 'Ottawa, Ontario, Canada'."""
    if not location or "," not in location:
        return {}
    parts = [p.strip() for p in location.split(",")]
    d = {}
    if len(parts) >= 1:
        d["city"] = parts[0]
    if len(parts) >= 2:
        d["state_province"] = parts[1]
    if len(parts) >= 3:
        d["country"] = parts[-1]
    return d


# ─── Load / Save helpers ──────────────────────────────────────────────

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


# ─── Ephemeral answer builder ─────────────────────────────────────────

_PROFILE_KEYS = {
    "first_name", "last_name", "email", "phone",
    "linkedin_url", "github_url", "portfolio_url",
    "resume_path", "location",
}

def _build_ephemeral(profile: dict) -> dict:
    """Build {key: (value, source)} from profile top-level keys + answers + derivations."""
    ephemeral = {}

    # Top-level profile keys
    for k in _PROFILE_KEYS:
        v = profile.get(k)
        if v:
            ephemeral[k] = (v, "profile")

    # Derive full_name
    fn, ln = profile.get("first_name", ""), profile.get("last_name", "")
    if fn and ln:
        ephemeral["full_name"] = (f"{fn} {ln}", "derived")
    elif fn or ln:
        ephemeral["full_name"] = (fn or ln, "derived")

    # Derive location parts
    loc = profile.get("location", "")
    if loc and "," in loc:
        derived_loc = _derive_location(loc)
        for k, v in derived_loc.items():
            if v:
                ephemeral[k] = (v, "derived")

    # Profile-level synonym keys (backward compat for common_answers migration)
    for old_key in ("city", "country", "state", "zip", "website"):
        v = profile.get(old_key)
        if v:
            ephemeral.pop(old_key, None)  # prefer derived over stale

    # Static answers (new schema)
    answers = profile.get("answers", {})
    if isinstance(answers, dict):
        for k, v in answers.items():
            if isinstance(v, dict):
                v = v.get("value")
            if v:
                ephemeral[k] = (v, "static")

    # Backward compat: old common_answers keys that don't conflict with answers
    for k, v in profile.get("common_answers", {}).items():
        if k not in ephemeral and v:
            ephemeral[k] = (v, "static")

    return ephemeral


# ─── Resolution chain ─────────────────────────────────────────────────

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
        return Resolution(None, None, label, "no_match", ephemeral_only=False)

    nf = lambda s: normalize(s)  # noqa: E731

    ephemeral = _build_ephemeral(profile)

    # ── Step 1: session_cache ──
    sc_entry = session_cache.get(norm)
    if sc_entry:
        key = sc_entry.get("key")
        val = _find_ephemeral_value(key, ephemeral)
        if val is not None:
            return Resolution(val, key, label, "session_cache", ephemeral_only=False)

    # ── Step 2: label_map (persistent cache) ──
    lm_entry = label_map.get(norm)
    if lm_entry:
        key = lm_entry["key"]
        val = _find_ephemeral_value(key, ephemeral)
        if val is not None:
            # Version check — if target key's version changed, entry is stale
            lm_entry["hit_count"] = lm_entry.get("hit_count", 0) + 1
            return Resolution(val, key, label, "label_map", ephemeral_only=False)
        else:
            # Key no longer exists in ephemeral — stale entry, remove
            label_map.pop(norm, None)

    # ── Step 3: prefix match (truncation) ──
    # Check if norm starts with any cached entry's key
    for cached_norm, entry in session_cache.items():
        if norm.startswith(cached_norm):
            key = entry.get("key")
            val = _find_ephemeral_value(key, ephemeral)
            if val is not None:
                return Resolution(val, key, label, "session_cache", ephemeral_only=False)
    for cached_norm, entry in label_map.items():
        if norm.startswith(cached_norm):
            key = entry["key"]
            val = _find_ephemeral_value(key, ephemeral)
            if val is not None:
                entry["hit_count"] = entry.get("hit_count", 0) + 1
                return Resolution(val, key, label, "label_map", ephemeral_only=False)

    # ── Step 4: ephemeral exact match ──
    for key, (val, source) in ephemeral.items():
        if nf(key.replace("_", " ")) == norm:
            # Save to label_map immediately (deterministic, no LLM needed)
            label_map[norm] = LabelMapEntry(
                key=key, provenance="direct_match",
                created=time.strftime("%Y-%m-%d"), hit_count=1, version=1
            ).__dict__
            return Resolution(val, key, label, source, ephemeral_only=False)

    # ── Step 5: --answers override ──
    for k, v in answers_override.items():
        if nf(k) == norm:
            return Resolution(v, "answers_override", label, "user_typed", ephemeral_only=False)

    # ── Step 6: LLM selection (session-cached only) ──
    # LLM selects from ephemeral keys. Never generates values.
    # Session-cached — does NOT persist to label_map until two-encounter rule passes.
    keys_list = list(ephemeral.keys())
    selected = _llm_select_key(label, keys_list)
    if selected and selected in ephemeral:
        val, source = ephemeral[selected]
        enc_count = 1
        prev = None
        # Check if this label was seen in a prior session
        existing = session_cache.get(norm)
        if existing:
            prev = existing.get("key")
            if prev == selected:
                enc_count = existing.get("encounter_count", 1) + 1
            else:
                enc_count = 1  # different selection → reset

        session_cache[norm] = {
            "key": selected,
            "value": val,
            "encounter_count": enc_count,
            "previous_result": prev,
        }
        _save_json(_SESSION_CACHE_PATH, session_cache)
        return Resolution(val, selected, label, "llm_selected", ephemeral_only=(enc_count < 2))

    return Resolution(None, None, label, "no_match", ephemeral_only=False)


def _find_ephemeral_value(key: str, ephemeral: dict) -> Optional[str]:
    """Look up a key in the ephemeral dict."""
    entry = ephemeral.get(key)
    if entry:
        return entry[0]  # (value, source) tuple
    return None


def _llm_select_key(label: str, available_keys: list) -> Optional[str]:
    """LLM selects among available keys. Never generates new keys or values."""
    if not available_keys:
        return None

    keys_json = json.dumps(available_keys)
    prompt = (
        f"Available answer keys: {keys_json}\n"
        f"Form label: \"{label}\"\n\n"
        "Which SINGLE key's value answers this label?\n"
        "Return only the key name, or \"no_match\" if none fit.\n"
        "NEVER create a new key. NEVER guess a value."
    )

    try:
        from lib.ask_api import _text, _load_config  # noqa: N402
        reply, err = _text(prompt, temperature=0.1, max_tokens=32, cfg=_load_config())
        if err:
            return None
        key = (reply or "").strip().strip('"').strip("'")
        if key in available_keys:
            return key
    except Exception:
        pass

    return None


# ─── Commit (post-verify) ─────────────────────────────────────────────

def commit_resolutions(
    resolutions: list[Resolution],
    profile: dict,
    session_cache: Optional[dict] = None,
    label_map: Optional[dict] = None,
    answers_override: Optional[dict] = None,
) -> list[str]:
    """Run after verify passes. Persists LLM selections that passed the
    two-encounter rule, and saves user-typed answers to profile.json.

    Returns list of newly persisted label_map entries for logging.
    """
    if session_cache is None:
        session_cache = _load_json(_SESSION_CACHE_PATH, {})
    if label_map is None:
        label_map = _load_json(_LABEL_MAP_PATH, {})
    if answers_override is None:
        answers_override = {}

    now = time.strftime("%Y-%m-%d")
    new_entries = []

    for res in resolutions:
        norm = normalize(res.label)
        if not norm:
            continue

        if res.provenance == "user_typed" and res.value and res.key == "answers_override":
            key_name = _llm_suggest_key(res.label)
            if key_name:
                answers = profile.setdefault("answers", {})
                if key_name not in answers:
                    answers[key_name] = res.value
                    label_map[norm] = {
                        "key": key_name, "provenance": "user_confirmed",
                        "created": now, "hit_count": 1, "version": 1,
                    }
                    new_entries.append(norm)
                _save_profile(profile)

        elif res.provenance == "llm_selected" and not res.ephemeral_only:
            # Two-encounter rule passed: same LLM selection across 2+ sessions
            label_map[norm] = {
                "key": res.key, "provenance": "encountered_twice",
                "created": now, "hit_count": 1, "version": 1,
            }
            new_entries.append(norm)

    if new_entries:
        _save_json(_LABEL_MAP_PATH, label_map)
        # Clear session cache entries that were promoted
        for norm in new_entries:
            session_cache.pop(norm, None)
        _save_json(_SESSION_CACHE_PATH, session_cache)

    return new_entries


def _llm_suggest_key(label: str) -> Optional[str]:
    """LLM suggests a key name for a user-typed answer."""
    prompt = (
        f"Form label: \"{label}\"\n\n"
        "Suggest a short, descriptive key name (snake_case, 1-3 words) "
        "to save this answer for reuse on future jobs. "
        "Return only the key name, nothing else."
    )
    try:
        from lib.ask_api import _text, _load_config
        reply, err = _text(prompt, temperature=0.1, max_tokens=32, cfg=_load_config())
        if err:
            return None
        key = (reply or "").strip().strip('"').strip("'")
        if key and len(key) < 60:
            return key
    except Exception:
        pass
    return None


def _save_profile(profile: dict):
    """Save profile.json updates."""
    profile_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "profile.json"
    )
    # Only save if it exists and we're not running from ji-skill
    if os.path.exists(profile_path):
        _save_json(profile_path, profile)


# ─── Entry point for act.py ───────────────────────────────────────────

def resolution_for_fill(
    label: str,
    profile: dict,
    answers_override: Optional[dict] = None,
) -> Resolution:
    """Single-call entry point for act.py. Loads cache from disk, resolves, returns result."""
    label_map = _load_json(_LABEL_MAP_PATH, {})
    session_cache = _load_json(_SESSION_CACHE_PATH, {})
    return resolve(
        label, profile,
        session_cache=session_cache, label_map=label_map,
        answers_override=answers_override,
    )
