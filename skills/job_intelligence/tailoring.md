# Tailoring — data/build separation

Context pointer from `SKILL.md`. Load only when running the tailoring stage.

The LLM writes a `resume.json` data file (no code). A shared builder (`lib/build_resume.py`) reads the JSON and produces PDFs. The JSON is easy to review for quality issues (pandering, hallucination, title creep) without parsing code.

Two backends via `JI_TAILOR` env var:

- **`JI_TAILOR=agent`** (default): Prompt printed to stdout. Write `resume.json` in [JSON Resume](https://jsonresume.org/) format (standard — LLMs already know it). Build and admit:
  ```
  python -m lib.build_resume results/<jid>/resume.json results/<jid>   # validate + PDFs
  tailor.py admit <jid>                # grounding gate + advance stage
  ```
  The LLM reads the generated resume.json and PDFs to judge quality before admitting.
- **`JI_TAILOR=gem`**: Uses Gemini Web gem. Gem generates `resume.json`, builder runs inline. Run `admit <jid>` to confirm + advance stage.

Both routes converge on `admit` — gem route auto-builds PDFs, agent route requires manual build step. `admit` advances DB stage to "tailored" (CV ready). Apply pipeline advances to "applied" (form submitted).

**Cover letter** is stored in the `coverLetter` field of the JSON Resume (custom extension). The builder automatically generates a separate Cover Letter PDF if this field is present. The cover letter text is plain text (no markdown), maximum 3 paragraphs, no logistics (salary, availability dates).

Prompt does not include default resume — fill in CV content from candidate profile + job requirements.

## Quality Review

After tailoring, optionally review generated CVs before admitting:

```
tailor.py review [--jobs N]
```

Shows the job title, URL, and cover letter from the generated PDF. Run `report.py inspect <jid>` for the full strategy.
If the cover letter or CV needs fixes: `retry <jid> --feedback "what to fix"` — injects feedback + previous response and re-tailors immediately.

The `resume.json` data file lives at `results/<jid>/resume.json`. You can read it directly to check for quality issues: does it mention the company name? Does it pander? Are there fabricated metrics? JSON is easier to audit than fpdf2 code.

The builder also validates the JSON before generating PDFs — run `python -m lib.build_resume --validate <resume.json>` to check without building.
