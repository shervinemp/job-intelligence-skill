"""Platform: jobright.ai — description extractor using DOM selectors."""


def pre_fetch(page):
    pass


def extract_text(page):
    return page.evaluate("""() => {
      const sections = document.querySelectorAll('.index_sectionContent__prVJT');
      if (sections.length) {
        return Array.from(sections).map(s => s.innerText).join('\\n\\n');
      }
      const main = document.querySelector('.index_jobDetailContent__rhs3U');
      if (main) return main.innerText;
      return document.body.innerText;
    }""")
