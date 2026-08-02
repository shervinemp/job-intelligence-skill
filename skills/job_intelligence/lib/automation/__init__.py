"""lib/automation — reusable core for autonomous pipelines.

The SAFE abstractions extracted from the apply pipeline — abstractions
that add evidence and decision protocol but never encode domain
expectations (those stay in the consuming skill):

  obs        — always-on structured event log (session files)
  normalize  — accent/case/punctuation folding (pure)
  llm        — expectation-free option selection via the local LLM
  diff       — consecutive-run comparison (the regression canary)
  dossier    — the standard handover format (fields/blockers/decisions)

Domain-calibrated constants (scoring thresholds, aliases, hint lists)
deliberately live OUTSIDE this package.
"""
