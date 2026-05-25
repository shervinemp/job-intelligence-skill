"""Platform: jobright.ai — description extractor using DOM selectors."""

import re


def pre_fetch(page):
    pass


def extract_text(page):
    try:
        page.wait_for_selector("#jobs-page-main-content", timeout=10000)
    except Exception:
        pass
    return page.evaluate("""() => {
      const el = document.querySelector('#jobs-page-main-content > div > section > div');
      if (el) return el.innerText;
      const main = document.querySelector('.index_jobDetailContent__rhs3U');
      if (main) return main.innerText;
      return document.body.innerText;
    }""")


def clean(text):
    text = re.sub(
        r"(?im)^.*?(insider connection|email credits available|"
        r"beyond your network|find more connections|find any email|"
        r"from your previous company|from your school).*$\n?",
        "", text,
    )
    return text.strip()
