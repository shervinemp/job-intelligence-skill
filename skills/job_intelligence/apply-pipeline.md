# Apply pipeline — phased walkthrough

Context pointer from `SKILL.md`. Load when running an apply (`apply.py detect → act → verify`). The step table lives in SKILL.md; this file is the mental model for HOW to run each phase.

```
─── PHASE 1: RECONNAISSANCE ───
detect → Read the page. Classify the type. Do NOT fill anything.
          Output: TYPE: easy_apply / ats_direct / external / login_wall

─── PHASE 2: FIELD INVENTORY ───
act --fill → Catalogs every field on the page.
                        Note fields resolved automatically (✅) vs.
                        fields needing your input (❓).
                        Do NOT provide --answers yet.
                        Output: categorized DRY_RUN listing.

─── PHASE 3: TARGETED FILLING ───
act --fill --answers '{"label": "value"}' → Fill fields marked ❓.
    • Fill ALL required fields in one shot. SPA forms wipe everything on validation error.
    • Only fill fields you're confident about.
    • Check every value against profile answers (rule 10).
    • Leave salary, dates, referral source unfilled unless profile has them.
    • The preview shows provenance (profile/answers/derived/auto_decline)
      for every value. Check it before confirming.
    • If unfilled remain, repeat with more --answers.

─── PHASE 4: NAVIGATE & REPEAT ───
act --next → Advance to next page.
             If submission detected → routed to --submit.
             If validation errors → routed back to --fill.

─── PHASE 4.5: PREVIEW (MANDATORY) ───
act --submit → Read every value in the preview against the
               Profile answers block (rule 7).

─── PHASE 5: SUBMIT (ONE-SHOT) ───
act --submit → Pre-submit check runs automatically. Submit is clicked
               ONCE (submit_clicked recorded first). Uncertain outcome →
               next run INVESTIGATES, never re-clicks; only --force after
               human confirms failure (rules 12-13).

─── PHASE 6: VERIFY ───
verify → Confirm the application was received (rule 4). Never skip
         this step; after STATUS "uncertain" check email or the ATS
         page for confirmation.
```

Rules cited above live in `SKILL.md` → Orchestrator rules — the single source.
