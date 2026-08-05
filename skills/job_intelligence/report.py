#!/usr/bin/env python3
"""Report CLI — Inspect, export, and manage pipeline data.

Every line below is enforced by scripts/lint.py: a command documented here
must dispatch in lib/report.py, and a command that dispatches must be
documented here. (This block used to advertise `shell`, `companies` and
`contacts`, none of which existed, while omitting thirteen that did — and
it is printed verbatim on an unknown command, so it taught the wrong
surface at exactly the moment someone was lost.)

Usage:
  DECISIONS
    python3 report.py handovers [OWNER]       The inbox: open decisions by owner
    python3 report.py help                    Grouped surface map
    python3 report.py widgets                 Unhandled widget-class backlog

  CORRECTNESS  (ETHOS §10 — the falsification instrument)
    python3 report.py adjudicate [--limit N] [--platform P]
                                              Fills awaiting a verdict, riskiest first
    python3 report.py adjudicate <id> correct|wrong|unanswerable [note]
    python3 report.py wrongfill [--platform P] Wrong-fill rate + its denominator
    python3 report.py spc [check|unpause <platform>] Wrong-fill SPC tripwire
    python3 report.py ingest <resume.txt>       Profile ingestion draft (C1)
    python3 report.py widget-draft [<artifact>] Widget-handler draft (D1)
    python3 report.py domains [approve|deny] <host>  New-domain approval gate (F2)
    python3 report.py applied [--unconfirmed]  Post-submit confirmation (G2)
    python3 report.py applied-confirm <jid>     Record submission confirmed
    python3 report.py fleet-scan                Scan dossiers for wrong values (URN etc.)

  EVIDENCE
    python3 report.py inspect <jid>           Full job details
    python3 report.py handoff <jid>           Latest dossier for a job
    python3 report.py audit <jid>             Field-level audit summary
    python3 report.py diff <jid>              Regression diff vs previous run
    python3 report.py observe <jid>           Observation timeline for a job
    python3 report.py session [run_id]        Event log for a run
    python3 report.py glossary                Vocabulary (generated from terms.py)

  FLEET
    python3 report.py shadow [--classify]     Shadow outcomes / owner split
    python3 report.py fleet                   Accuracy, attribution, trend
    python3 report.py candidates [--limit N]  Tailored jobs ready to apply
    python3 report.py pending [--stage S]     Active jobs at a gate stage
    python3 report.py rules list|add|promote|clear    Runtime alias rules (wired loop)
    python3 report.py keywords list|add              Runtime classifier keywords

  READINESS
    python3 report.py stats                   Pipeline statistics
    python3 report.py profile                 Profile coverage
    python3 report.py summary [--days N]      Recent activity digest
    python3 report.py search <query>          Search jobs
    python3 report.py export json|csv [--stage S]
    python3 report.py events [--upcoming]     List events
    python3 report.py archive                 Archive state/registry for reset jobs
"""
from lib.report import main

if __name__ == "__main__":
    main()
