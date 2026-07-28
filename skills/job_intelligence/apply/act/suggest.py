"""act/suggest.py — Pre-fill LLM label→key mapping.

Probes the form, runs the LLM to map field labels to profile KEYS
(not values), and returns a label→value dict ready for --answers.
The heuristic resolve always runs first — only unresolvable fields
are sent to the LLM.
"""
import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def llm_field_key_mapping(fields, profile, job=None, ephemeral=None):
    """LLM maps field labels to profile KEYS. Returns label→value dict.

    The LLM never outputs raw values — it maps each field label to a
    profile KEY. The system looks up the actual value from the profile.
    Job context (title, company, description) helps disambiguate.
    Accepts an optional pre-built ephemeral dict to avoid redundant builds.
    """
    if not fields:
        return {}
    from lib.ask_api import ask_text, available as llm_avail
    if not llm_avail():
        return {}
    if ephemeral is None:
        from apply.common.resolve import _build_ephemeral
        ephemeral = _build_ephemeral(profile)
    if not ephemeral:
        return {}

    job = job or {}
    lines = [f"Job: {job.get('title', '')}"]
    c = job.get("company", "")
    loc = job.get("location", "")
    desc = job.get("description", "")
    if c:
        lines.append(f"Company: {c}")
    if loc:
        lines.append(f"Location: {loc}")
    if desc:
        lines.append(f"Description: {desc[:500]}")
    lines.append("")
    lines.append("Available profile data (key → value):")
    for k, (v, src) in sorted(ephemeral.items()):
        lines.append(f"  {k}: {str(v)[:80]}")
    lines.append("")
    lines.append("Map each form field to its BEST matching profile KEY:")
    for f in fields:
        label = (f.get("label") or "").strip()
        tag = f.get("tag", f.get("type", "")).upper()
        opts = f.get("options", [])
        if not label:
            continue
        parts = [f"  field: {label[:80]}"]
        if tag or opts:
            parts.append(f"  type: {tag}")
            if opts:
                parts.append(f"  options: {opts[:10]}")
        lines.extend(parts)
    lines.append("")
    lines.append(
        "Return a JSON object mapping each field label to a profile KEY. "
        "Example: {\"Select your country of employment\": \"country\"}. "
        "Only use profile keys from the list above — never invent new ones. "
        "If no profile key matches, set value to null. "
        "Return ONLY the JSON."
    )
    prompt = "\n".join(lines)
    reply, err = ask_text(prompt, temperature=0.1, max_tokens=2048)
    if err or not reply:
        return {}
    try:
        text = reply.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        result = {}
        for label, profile_key in data.items():
            if not profile_key or not isinstance(profile_key, str):
                continue
            entry = ephemeral.get(profile_key)
            if entry is not None:
                result[label] = entry[0]
        if result:
            for label, val in result.items():
                print(f"    DIAG: LLM_MAP | {label[:50]} | {val[:50]}",
                      file=sys.stderr)
        return result
    except (json.JSONDecodeError, TypeError):
        return {}
