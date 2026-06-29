"""ATS Plugin base class — formalizes the existing hook module pattern."""
import re
from typing import Optional


class ATSPlugin:
    """Override or augment apply behavior for a specific ATS.

    Subclasses set `name` and `patterns` (URL regexes). Only override
    the hooks you need — defaults are no-ops.

    The existing registry system (registry.py _load_hook_module) loads
    .py files from the registry/ directory by platform name. This base
    class provides a standard interface for those hook modules.
    """
    name: str = ""
    patterns: list[str] = []
    priority: int = 0

    def detect(self, page) -> Optional[dict]:
        """Return {ats_type, ...} or None. Default matches by pattern."""
        url = page.url().lower()
        for p in self.patterns:
            if re.search(p, url):
                return {"ats_type": self.name}
        return None

    def pre_fill(self, page):
        """Called before filling fields on a page."""
        pass

    def post_fill(self, page):
        """Called after filling all fields on a page."""
        pass

    def upload_documents(self, page, jid):
        """Upload documents (resume, cover letter) if needed."""
        pass

    def pre_submit(self, page):
        """Called before clicking submit."""
        pass

    def post_submit(self, page) -> dict:
        """Called after submit. Return dict with result info."""
        return {}
