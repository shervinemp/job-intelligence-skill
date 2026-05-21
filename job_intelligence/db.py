#!/usr/bin/env python3
"""DB CLI — Inspect, export, and manage the job pipeline database.

Usage:
  python3 db.py shell                     Open SQLite shell
  python3 db.py stats                     Pipeline statistics
  python3 db.py inspect <jid>             Full job details
  python3 db.py search <query>            Search jobs
  python3 db.py export json [--stage S]   Export jobs as JSON
  python3 db.py export csv [--stage S]    Export jobs as CSV
  python3 db.py summary [--days N]        Recent activity digest
  python3 db.py companies [query]         List/search companies
  python3 db.py events [--upcoming]       List events
  python3 db.py contacts <jid>            Contacts for a job
"""
import sys
from lib.cmd_db import main

if __name__ == "__main__":
    main()
