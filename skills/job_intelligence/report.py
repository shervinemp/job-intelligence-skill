#!/usr/bin/env python3
"""Report CLI — Inspect, export, and manage pipeline data.

Usage:
  python3 report.py shell                     Open SQLite shell
  python3 report.py stats                     Pipeline statistics
  python3 report.py inspect <jid>             Full job details
  python3 report.py search <query>            Search jobs
  python3 report.py export json [--stage S]   Export jobs as JSON
  python3 report.py export csv [--stage S]    Export jobs as CSV
  python3 report.py summary [--days N]        Recent activity digest
  python3 report.py companies [query]         List/search companies
  python3 report.py events [--upcoming]       List events
  python3 report.py contacts <jid>            Contacts for a job
"""
import sys
from lib.report import main

if __name__ == "__main__":
    main()
