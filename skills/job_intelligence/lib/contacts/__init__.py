"""lib/contacts — Contact discovery orchestration.

Discovers contacts for a job from multiple sources:
  1. Recruiters from job page (existing linkedin.py)
  2. Team members from company LinkedIn
  3. My connections at the company
  4. Email suggestions via LLM
"""

from .discover import discover_contacts

__all__ = ["discover_contacts"]
