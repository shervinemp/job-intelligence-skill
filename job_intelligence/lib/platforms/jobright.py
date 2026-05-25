"""Platform: jobright.ai — description extractor using DOM selectors."""

import re


def pre_fetch(page):
    pass


def extract_text(page):
    try:
        page.wait_for_selector(".index_jobDetailContent__rhs3U", timeout=10000)
    except Exception:
        pass
    return page.evaluate("""() => {
      const all = document.querySelectorAll('.index_sectionContent__prVJT');
      const sections = Array.from(all).filter(s => {
        const h2 = s.querySelector('h2.index_label__MLcbM');
        return h2 && !h2.title.includes('Insider Connection');
      });
      if (sections.length) {
        return sections.map(s => s.innerText).join('\\n\\n');
      }
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
