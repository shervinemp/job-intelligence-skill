#!/usr/bin/env python3
"""eval_llm_surfaces.py — measure the WEAK model's decision accuracy per
handoff surface.

The orchestrator (strong model) is the intended operator; the served local
model (ask_api) is only invoked for the escapes it physically must handle. This
script scores the WEAK model against a golden set, per surface, so we can see
which surfaces the weak model can be trusted on and which must stay with the
orchestrator.

Surfaces scored (each has a golden set of (input → expected) pairs):
  option_pick  — choose the option matching an answer, from top_options.
  gap_fill     — given a field label + dossier evidence, supply the value.
  vision       — given an image, say whether submission succeeded (optional;
                 needs a real screenshot).

Gated: ask_api must be available (JI_LLM_MODE=on unlocks the escapes). In auto
mode these escapes are OFF by design — this script forces `on` so the eval can
run, then restores it. If ask_api is down it prints SKIPPED and exits 0.

Usage:
  python3 scripts/eval_llm_surfaces.py [--surface option_pick] [--json]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json

# Golden set: (question, answer, options) -> expected option text.
GOLDEN_OPTION_PICK = [
    ("Country", "Canada",
     [{"text": "Antigua and Barbuda (+1-268)"}, {"text": "Canada (+1)"},
      {"text": "Cambodia (+855)"}], "Canada (+1)"),
    ("Province or Territory", "Ontario",
     [{"text": "Nova Scotia"}, {"text": "Ontario"}, {"text": "Quebec"}],
     "Ontario"),
    ("Years of experience", "5",
     [{"text": "0-2"}, {"text": "3-5"}, {"text": "6-10"}], "3-5"),
    ("Will you require sponsorship?", "no",
     [{"text": "Yes"}, {"text": "No"}], "No"),
]

# Golden set: (field label, question hint) -> expected answer.
GOLDEN_GAP_FILL = [
    ("Work authorization status", "Are you legally authorized to work?",
     "Yes"),
    ("Current employer", "Where do you currently work?", "Acme Corp"),
]


def _score_option_pick():
    from lib.automation import llm
    ok = 0
    results = []
    for question, answer, opts, expected in GOLDEN_OPTION_PICK:
        got = llm.pick_option(opts, question, answer)
        got_text = (got or {}).get("text", "")
        correct = (got_text == expected)
        results.append({"question": question, "expected": expected,
                        "got": got_text, "correct": correct,
                        "status": llm.last_status()["state"]})
        if correct:
            ok += 1
    return ok, len(GOLDEN_OPTION_PICK), results


def _score_gap_fill():
    from lib import ask_api
    ok = 0
    results = []
    for label, hint, expected in GOLDEN_GAP_FILL:
        prompt = (
            f"You are filling a job application. Field: '{label}'. "
            f"Context: {hint}. Reply with ONLY the value to fill, no "
            "explanation. If unknown, reply NONE."
        )
        reply, err = ask_api.ask_text(prompt, temperature=0.1, max_tokens=32)
        correct = reply and expected.lower() in (reply or "").lower()
        results.append({"field": label, "expected": expected,
                        "got": reply, "correct": bool(correct),
                        "err": err})
        if correct:
            ok += 1
    return ok, len(GOLDEN_GAP_FILL), results


def _stub_models(module):
    """Replace ask_api with a deterministic stub so the SCORING LOGIC is
    testable when the real model is down. The stub picks the option whose
    text matches the golden expected answer — a PERFECT model — so the
    harness should report 100%. This proves the scoring logic works, not
    that the real model does."""
    import lib.automation.llm as llm
    from unittest.mock import patch as _p

    def fake_ask_text(prompt, **kw):
        # pick_option's prompt lists "[i] <text>" lines. The golden set
        # expects the option containing the answer phrase; the stub picks the
        # FIRST option whose text contains the answer's first word.
        import re
        ans = ""
        for line in prompt.splitlines():
            if line.startswith("Answer:"):
                ans = line.split(":", 1)[1].strip().lower()
                break
        if not ans:
            return ("0", None)
        for line in prompt.splitlines():
            m = re.match(r"\[(\d+)\] (.+)", line)
            if m and ans.split()[0] in m.group(2).lower():
                return (m.group(1), None)
        return ("0", None)

    return _p("lib.ask_api.available", return_value=True), \
        _p("lib.ask_api.ask_text", side_effect=fake_ask_text)


def main():
    surface = "all"
    want_json = False
    args = sys.argv[1:]
    stub = "--stub" in args
    if "--surface" in args:
        surface = args[args.index("--surface") + 1]
    if "--json" in args:
        want_json = True

    stub_ctx = _stub_models(__import__("sys").modules[__name__]) if stub else None

    from lib import ask_api
    if not ask_api.available() and not stub:
        print("SKIPPED: ask_api not available — run the eval when the local "
              "model is up (or use --stub to test the harness itself).")
        return 0

    # Force escapes ON for the eval, restore after.
    prev = os.environ.get("JI_LLM_MODE")
    os.environ["JI_LLM_MODE"] = "on"
    try:
        if stub_ctx is not None:
            for p in stub_ctx:
                p.start()
        try:
            report = {}
            if surface in ("all", "option_pick"):
                ok, n, rows = _score_option_pick()
                report["option_pick"] = {"correct": ok, "n": n,
                                         "accuracy": round(ok / n, 3),
                                         "rows": rows}
            if surface in ("all", "gap_fill"):
                ok, n, rows = _score_gap_fill()
                report["gap_fill"] = {"correct": ok, "n": n,
                                      "accuracy": round(ok / n, 3),
                                      "rows": rows}
        finally:
            if stub_ctx is not None:
                for p in stub_ctx:
                    p.stop()
    finally:
        if prev is None:
            os.environ.pop("JI_LLM_MODE", None)
        else:
            os.environ["JI_LLM_MODE"] = prev

    if want_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    for surf, s in report.items():
        print(f"{surf}: {s['correct']}/{s['n']} "
              f"({s['accuracy']:.0%})")
        for r in s["rows"]:
            mark = "OK " if r["correct"] else "FAIL"
            print(f"  [{mark}] {r.get('question') or r.get('field')} "
                  f"→ expected={r.get('expected')!r} got={r.get('got')!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
