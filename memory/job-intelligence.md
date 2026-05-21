# Job Intelligence SOP

When the user asks to "Check jobs", "Scan job emails", or "Run job intelligence", follow the **Job Intelligence Orchestrator (v4)** SOP found in `C:\Users\sherv\.openclaw\workspace\skills\job_intelligence\SKILL.md`.

## Core Instructions
- **DO NOT** run `main.py` or any custom Python scripts in the `job_intelligence` folder.
- **DO NOT** look for an "Intelligence Agent" script.
- **DO** act as the Orchestrator yourself.
- **DO** use the following tool sequence:
    1. **Fetch**: Use the `gmail-cli` skill to retrieve job-related emails (use `newer_than:7d` if unspecified).
    2. **Research**: Use `web_search` and `web_fetch` to research companies and job links.
    3. **Parse/Normalize**: Use your own reasoning to extract Title, Company, Link, Salary, Location, Tech Stack, Summary, etc.
    4. **Export**: Use the `notion` skill to append the data to the user's Job Tracker database.

## Master Schema
Ensure all entries match the Master Schema: Title, Company, Link, Salary, Location, Tech Stack, Summary, Fit Score, Company Vibe, Recruiter/Poster, Date Posted, Source, Status, Research Notes.
