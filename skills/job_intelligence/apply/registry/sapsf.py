"""SAP SuccessFactors platform hooks."""
import os, time


def pre_fill(page):
    """Expand all sections before filling."""
    for section in ["Profile Information", "Language Skills", "Job-Specific Information"]:
        try:
            btn = page.locator(f'button:has-text("{section}")')
            if btn.count():
                btn.first.click(timeout=3000)
                time.sleep(0.5)
        except Exception:
            pass
    try:
        link = page.locator('a:has-text("Expand all sections"), button:has-text("Expand all sections")')
        if link.count():
            link.first.click(timeout=3000)
            time.sleep(1)
    except Exception:
        pass


def upload_resume(page, jid):
    """Upload tailored resume to SAP SF custom widget."""
    from lib.config import RESULTS_DIR
    res_dir = os.path.join(RESULTS_DIR, jid)
    if not os.path.isdir(res_dir):
        return False
    pdfs = [f for f in os.listdir(res_dir) if "Resume" in f and f.endswith(".pdf")]
    if not pdfs:
        return False
    pdf_path = os.path.join(res_dir, pdfs[0])
    try:
        upload_btn = page.locator('text=Resume, text=Upload, text=CV').first
        if not upload_btn.count():
            upload_btn = page.locator('[class*="upload"], [class*="resume"], [class*="file"]').first
        if not upload_btn.count():
            return False
        with page.expect_file_chooser() as fc_info:
            upload_btn.click(force=True, timeout=5000)
        fc = fc_info.value
        fc.set_files(pdf_path)
        time.sleep(2)
        return True
    except Exception:
        return False


def post_fill(page):
    """After native setter fills combobox INPUTs, notify SAP SF's juic
    framework by firing its internal change handler for each field."""
    page.evaluate("""() => {
        const boxes = document.querySelectorAll('input[role="combobox"]');
        boxes.forEach((el, i) => {
            if (!el.value || el.value.length === 0) return;
            setTimeout(() => {
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
                const id = el.id;
                if (id && window.juic && window.juic.fire) {
                    window.juic.fire(id + ':', '_handleChange', new Event('change'));
                }
            }, i * 200);
        });
    }""")
    count = page.evaluate("() => document.querySelectorAll('input[role=\"combobox\"]').length")
    time.sleep(count * 0.25 + 1)


def pre_submit(page):
    """Prepare the page for submission."""
    pre_fill(page)
    time.sleep(1)
    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
