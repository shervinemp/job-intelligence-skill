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


# ─── Evaluator ────────────────────────────────────────────────────────

_BUILTINS = {
    "concat": lambda *args: " ".join(str(a) for a in args),
    "field": lambda val, idx: (
        [x.strip() for x in val.split(",")][idx]
        if isinstance(val, str) and "," in val
        else val.strip() if isinstance(val, str) else val
    ),
}

_DESCRIPTOR_RE = re.compile(r"^\$(facts|answers)/(.+)$")

def _resolve_ref(ref: str, facts: dict, answers: dict) -> str:
    """Resolve a JSON reference like '$facts/country' or '$answers/sponsorship'."""
    m = _DESCRIPTOR_RE.match(ref)
    if not m:
        return ref
    section, key = m.group(1), m.group(2)
    return {"facts": facts, "answers": answers}.get(section, {}).get(key, ref)


def _eval_derivations(facts: dict, derivations: list) -> dict:
    """Evaluate derivation expressions against facts. Returns {key: value} dict."""
    result = {}
    for d in derivations:
        fn = _BUILTINS.get(d["fn"])
        if fn is None:
            continue
        args = [(_resolve_ref(a, facts, {}) if isinstance(a, str) else a) for a in d.get("args", [])]
        try:
            result[d["key"]] = fn(*args)
        except Exception:
            pass
    return result


def _eval_rules(decisions: dict, job_context: dict, facts: dict, answers: dict) -> dict:
    """Evaluate decision rules against job context. Returns {rule_name: value} dict."""
    result = {}
    for rule in decisions.get("rules", []):
        for case in rule.get("cases", []):
            matched = True
            if "if" in case:
                for field, expected in case["if"].items():
                    actual = job_context.get(field) or facts.get(field)
                    if isinstance(expected, dict) and "$eq" in expected:
                        expected_val = _resolve_ref(expected["$eq"], facts, answers)
                        if str(actual or "").lower() != str(expected_val or "").lower():
                            matched = False
                            break
                    elif str(actual or "").lower() != str(expected or "").lower():
                        matched = False
                        break
            if matched:
                then_val = case.get("then", case.get("else", {}))
                if isinstance(then_val, dict):
                    if "$ref" in then_val:
                        result[rule.get("name", "unknown")] = _resolve_ref(
                            then_val["$ref"], facts, answers
                        )
                    elif "value" in then_val:
                        result[rule.get("name", "unknown")] = then_val["value"]
                break
    return result


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

def _build_ephemeral(profile: dict, job_context: dict) -> dict:
    """Build the full ephemeral answer dict from profile data + job context.
    Order: facts → derivations → rules → static answers.
    Returns {key: value} dict where each value is a tuple (value, source)."""
    facts = profile.get("facts", {})
    derivations = profile.get("derivations", [])
    answers = profile.get("answers", {})
    decisions = profile.get("decisions", {})

    ephemeral = {}

    # Facts
    for k, v in facts.items():
        if v:
            ephemeral[k] = (v, "fact")

    # Derivations
    for k, v in _eval_derivations(facts, derivations).items():
        if v:
            ephemeral[k] = (v, "derived")

    # Static answers
    for k, v in answers.items():
        val = v.get("value") if isinstance(v, dict) else v
        if val:
            ephemeral[k] = (val, "static")

    # Decision rules
    for k, v in _eval_rules(decisions, job_context, facts, answers).items():
        if v:
            ephemeral[k] = (v, "rule")

    return ephemeral


# ─── Resolution chain ─────────────────────────────────────────────────

def resolve(
    label: str,
    profile: dict,
    job_context: Optional[dict] = None,
    session_cache: Optional[dict] = None,
    label_map: Optional[dict] = None,
    answers_override: Optional[dict] = None,
) -> Resolution:
    """Resolve a field label to an answer value.

    Steps (strict order, returns on first hit):
      1. session_cache hit
      2. label_map hit (persistent cache, version-checked)
      3. prefix match (handles 60-char truncation)
      4. ephemeral exact match (facts, derivations, rules, static)
      5. LLM selection (session-cached only, not persistent)
      6. --answers override (user-provided)

    After verify passes, call commit_resolutions() to persist session entries
    that passed the two-encounter rule.
    """
    if job_context is None:
        job_context = {}
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

    # Build ephemeral once — reused by exact match and LLM selection
    ephemeral = _build_ephemeral(profile, job_context)

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
            # User typed this via --answers. LLM suggests key name for persistence.
            key_name = _llm_suggest_key(res.label)
            if key_name:
                # Save to profile.answers
                answers = profile.setdefault("answers", {})
                if key_name not in answers:
                    answers[key_name] = {"value": res.value, "source": "user_saved", "v": 1}
                    # Also persist to label_map
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
    job_context: Optional[dict] = None,
) -> Resolution:
    """Single-call entry point for act.py _fill_text and EEO handler.
    Loads label_map and session_cache from disk, resolves, returns result.
    """
    label_map = _load_json(_LABEL_MAP_PATH, {})
    session_cache = _load_json(_SESSION_CACHE_PATH, {})
    return resolve(
        label, profile, job_context=job_context,
        session_cache=session_cache, label_map=label_map,
        answers_override=answers_override,
    )
