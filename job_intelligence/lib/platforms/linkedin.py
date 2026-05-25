"""Platform: linkedin.com — pre-fetch and description cleaner."""

import re


def pre_fetch(page):
    try:
        btn = page.locator('[data-testid="expandable-text-button"]')
        if btn.is_visible(timeout=3000):
            btn.click()
            page.wait_for_timeout(1000)
    except Exception:
        pass


def clean(text):
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if "about the job" in line.strip().lower():
            text = "\n".join(lines[i:])
            break
    text = re.sub(
        r"(?im)^.*?(reactivate premium|show match details|tailor my resume|"
        r"help me stand out|use ai to assess|easy apply|"
        r"be an early applicant|no longer accepting applications).*$\n?",
        "", text,
    )
    idx = text.lower().find("set alert for similar jobs")
    if idx != -1:
        text = text[:idx]
    return text.strip()
