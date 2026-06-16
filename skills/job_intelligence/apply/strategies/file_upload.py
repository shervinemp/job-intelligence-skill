"""File upload strategy."""
import os, base64


def fill_file_upload(page, f, results_dir, jid, state):
    lbl_lower = (f.get("label") or "").lower()
    if not os.path.isdir(results_dir) or not any("Resume" in fn and fn.endswith(".pdf") for fn in os.listdir(results_dir)):
        return "unfilled"
    candidates = []
    for fn in os.listdir(results_dir):
        if "Resume" in fn and fn.lower().endswith(".pdf"):
            score = 0
            if (state.get("title") or "").split(" ")[0].lower() in fn.lower():
                score += 2
            if state.get("company", "").lower() in fn.lower():
                score += 1
            candidates.append((score, fn))
    candidates.sort(key=lambda x: -x[0])
    if not candidates:
        return None
    pdf_path = os.path.join(results_dir, candidates[0][1])
    try:
        if os.path.getsize(pdf_path) < 512:
            return "unfilled"
    except OSError:
        return None
    try:
        resume_inputs = [fi for fi in page.query_selector_all('input[type="file"]') if "resume" in (page.evaluate(f'(el) => el.closest("div,fieldset,section")?.textContent || ""', fi) or "").lower()]
        fi = resume_inputs[0] if resume_inputs else page.query_selector('input[type="file"]')
        if fi:
            fi.set_input_files(pdf_path)
            return "filled"
    except Exception:
        pass
    return None


def try_drag_drop(page, results_dir):
    candidates = [fn for fn in os.listdir(results_dir) if "Resume" in fn and fn.lower().endswith(".pdf")]
    if not candidates:
        return False
    pdf_path = os.path.join(results_dir, candidates[0])
    with open(pdf_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    data_url = f"data:application/pdf;base64,{b64}"
    try:
        return page.evaluate(f"""(dataUrl) => {{
            const dz = document.querySelector('.dropzone, [ondrop], [class*="file-upload"], [class*="drag-drop"], [class*="upload-resume"]');
            if (!dz) return false;
            return fetch(dataUrl).then(r => r.blob()).then(blob => {{
                const file = new File([blob], 'Resume.pdf', {{type: 'application/pdf'}});
                const dt = new DataTransfer();
                dt.items.add(file);
                ['dragenter', 'dragover'].forEach(t => {{
                    dz.dispatchEvent(new DragEvent(t, {{dataTransfer: dt, bubbles: true, cancelable: true}}));
                }});
                return dz.dispatchEvent(new DragEvent('drop', {{dataTransfer: dt, bubbles: true, cancelable: true}}));
            }}).catch(() => false);
        }}""", data_url)
    except Exception:
        return False
