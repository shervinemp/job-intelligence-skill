"""Extract Python scripts from Gemini response, run them, produce PDFs."""

import os
import re
import subprocess
import sys


def script_error(err):
    """Pretty-print a script execution error."""
    lines = err.split("\n") if err else []
    if lines:
        return lines[0].strip()[:120]
    return "Unknown error"


def extract_and_run(raw_output, app_dir):
    """Extract Python code from response, save scripts, run them to generate PDFs.
    Returns (saved_scripts, notes) where notes is a list of status strings."""
    saved_scripts = []

    if raw_output.startswith("Pro\n"):
        raw_output = raw_output[4:]

    code_blocks = re.findall(r"```python\s*(.*?)```", raw_output, re.DOTALL)
    if not code_blocks:
        m = re.search(r"Tailored gen\.py Script.*?\nPython\n((?:.|\n)+)", raw_output)
        if m:
            code_blocks = [m.group(1).strip()]
    notes = []

    for idx, code in enumerate(code_blocks, 1):
        code = code.strip()
        if len(code) > 100:
            fname = "script.py" if idx == 1 else f"script_{idx}.py"
            py_path = os.path.join(app_dir, fname)
            with open(py_path, "w", encoding="utf-8") as f:
                f.write(code)
            saved_scripts.append(py_path)

            try:
                r = subprocess.run(
                    [sys.executable, py_path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=app_dir,
                )
                pdfs = [f for f in os.listdir(app_dir) if f.endswith(".pdf")]
                if pdfs:
                    for pdf in pdfs:
                        notes.append(f"PDF: {os.path.join(app_dir, pdf)}")
                else:
                    notes.append(f"Script error: {script_error(r.stderr)}")
                    for f in os.listdir(app_dir):
                        fp = os.path.join(app_dir, f)
                        if f.endswith(".py") or f == "gemini_response.txt":
                            os.remove(fp)
                    saved_scripts.clear()
            except Exception as e:
                notes.append(f"Script run failed: {str(e)[:80]}")
                if not [f for f in os.listdir(app_dir) if f.endswith(".pdf")]:
                    for f in os.listdir(app_dir):
                        fp = os.path.join(app_dir, f)
                        if f.endswith(".py") or f == "gemini_response.txt":
                            os.remove(fp)
                    saved_scripts.clear()

    return saved_scripts, notes
