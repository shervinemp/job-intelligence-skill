"""lib/ingest.py — orchestrator-surface drafting hooks (LLM_GAPS.md).

Routing decision (ETHOS): the ORCHESTRATOR is the strong model. ask_api (the
local served model) is reserved for VISION — images/PDFs the orchestrator
cannot read as text — or inputs too large to surface in its context.

Therefore:
  C1 ingest_profile(text)   — surfaces the resume/export text as a FILE PATH
                              + summary. The orchestrator reads the file and
                              drafts profile.json. ask_api is used ONLY when
                              the input is an image/PDF (vision).
  D1 draft_widget_handler() — surfaces the failure-artifact path + capability
                              summary. The orchestrator reads the artifact file
                              and drafts the handler. No ask_api for the DOM
                              (the orchestrator reads it on demand).

Neither auto-writes. The LLM proposes; a human confirms; code owns.
"""
import json
import os


def _is_image(path):
    ext = (path or "").lower().rsplit(".", 1)[-1] if "." in (path or "") else ""
    return ext in ("png", "jpg", "jpeg", "gif", "webp", "pdf")


def ingest_surface(file_path, existing=None):
    """C1 — surface a resume/export file for the ORCHESTRATOR to draft from.

    Returns (surface, ok, detail) where surface is a dict the report command
    prints: {file, kind (text|image), size, summary (first lines / truncated),
    profile_present, next}. For TEXT the orchestrator reads the file and
    drafts; for IMAGE/PDF the orchestrator (or vision) reads the screenshot.
    ask_api text-drafting is intentionally NOT used — the orchestrator is the
    strong model.
    """
    if not file_path:
        return None, False, "no file path"
    try:
        size = os.path.getsize(file_path)
    except Exception as e:
        return None, False, f"cannot stat {file_path}: {e}"
    kind = "image" if _is_image(file_path) else "text"
    summary = ""
    if kind == "text":
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                summary = f.read(1200)  # a preview; orchestrator reads the rest
        except Exception as e:
            summary = f"(unreadable: {e})"
    return {
        "file": file_path,
        "kind": kind,
        "size": size,
        "summary": summary,
        "profile_present": bool(existing and existing.get("first_name")),
        "next": (f"read {file_path} and draft profile.json (orchestrator)"
                 if kind == "text" else
                 f"open {file_path} (image) — vision or the orchestrator "
                 f"reads it"),
    }, True, "surface ready"


def ingest_vision(file_path):
    """Vision-only profile ingestion: an image/PDF resume the orchestrator
    cannot read as text. Uses ask_api ONLY here (the sanctioned vision role).
    Returns (draft, ok, detail) for review — never auto-writes."""
    try:
        from lib.automation.llm import allow
        from lib.ask_api import available, ask
        if not (allow("vision") and available()):
            return None, False, ("vision gated or ask_api down — the "
                                 "orchestrator must read the image instead")
        reply, err = ask(file_path,
                         "Extract resume facts into JSON Resume profile "
                         "(name, email, phone, location, work_history, "
                         "education). Facts only, nothing invented.")
        if not reply:
            return None, False, f"vision returned nothing ({err or ''})"
        import re as _re
        m = _re.search(r"\{.*\}", reply, _re.S)
        if not m:
            return None, False, "vision reply had no JSON object"
        return json.loads(m.group(0)), True, "vision draft ready for review"
    except Exception as e:
        return None, False, f"vision ingest failed: {e}"


def draft_widget_surface(artifact_path=None):
    """D1 — surface a probe-failure artifact for the ORCHESTRATOR to draft a
    handler from. Returns (surface, ok, detail): the artifact path + capability
    summary. The orchestrator reads the artifact file (which may be a large
    DOM dump) directly and drafts the handler — no ask_api for the DOM."""
    try:
        from lib.config import JI_HOME
    except Exception:
        JI_HOME = os.path.expanduser("~/.ji")
    fails_dir = os.path.join(JI_HOME, "registry-failures")
    if not artifact_path:
        import glob as _glob
        arts = sorted(_glob.glob(os.path.join(fails_dir, "*.json")))
        if not arts:
            return None, False, "no probe-failure artifacts captured yet"
        artifact_path = arts[-1]
    cap, url = "", ""
    try:
        with open(artifact_path, encoding="utf-8") as f:
            art = json.load(f)
        cap = art.get("capability_summary", "")
        url = art.get("url", "")
    except Exception:
        pass
    return {
        "artifact": artifact_path,
        "capability": cap,
        "url": url,
        "next": (f"read {artifact_path} (DOM snapshot) and draft a "
                 f"widget-handler + registry entry + test — orchestrator"),
    }, True, "artifact surfaced for review"
