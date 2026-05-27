# 50 bugs & edge cases

## Critical (would crash or lose data)

1. **act --submit dry-run exits process** — `sys.exit(0)` on dry-run. If called from `cmd_auto`, kills the whole batch instead of returning.
2. **common_answers key vs normalized text mismatch** — `save_answer` stores keys with underscores (`previously_employed`). `_fill_radios` and `_fill_text` check `ck.lower() in q_norm` where `q_norm` has no underscores. Match always fails for underscore keys.
3. **Act --next rightmost fallback** — if only "Next" exists and it's disabled, `candidates` is empty, target is None. Falls through to disabled check which correctly reports "BUTTON_DISABLED". Not a bug, but fragile.
4. **Act --submit inside auto loop** — `sys.exit(0)` on dry-run kills auto loop. User can't batch-submit without --confirm on each.
5. **Fetch title regex** — HTML entities (`&amp;`) not decoded. Title ends up with encoded entities.
6. **Detect DB update on applied** — updates DB but doesn't call `save_state`. Next `act` loads stale state file.
7. **Act --fill radio id=""** — empty `id` produces `[id=""]` selector which matches unintended elements.
8. **Navigate leaves LinkedIn page open** — creates external page but never closes the original LinkedIn tab.
9. **Multiple dialogs** — `read_page` uses `document.querySelector('[role="dialog"]')`. If an error popup is layered over the form dialog, reads the wrong dialog.
10. **Act --auto state check** — reads state file for "submitted" string. String may not be present if modal closed without explicit "submitted" message.

## Medium (wrong behavior in edge cases)

11. **Craft_jid category default** — sets "general" for non-LinkedIn sources. Email Babcock jobs get "general" but should be rejected, not tailored.
12. **Craft_jid no save(state)** — sets default category in entry dict but doesn't call `save(state)`. Lost if process crashes.
13. **Fetch curl title** — `<title>` regex works on raw HTML. But SPA titles set via JS are missed.
14. **LinkedIn scraper title dedup** — exact string comparison. Trailing space or non-standard whitespace causes mismatch.
15. **Fetch save_description truncates at 8000** — title at 200. Reasonable but could truncate useful info.
16. **Act --fill resume upload** — picks first file matching "Resume" in results dir. May pick wrong job's resume.
17. **Act --fill follow checkbox** — unchecks ANY checkbox with "Follow" in label. Could uncheck a non-follow checkbox.
18. **Tailor retry doesn't handle DB locked** — SQLite concurrent write error not caught.
19. **Navigate platform detection** — based on URL string, not actual page. If URL redirects, platform detected from original URL.
20. **Auto loop crash** — no try/except in `cmd_auto`. Playwright timeout kills the entire batch.

## Minor (annoyances, not blockers)

21. **Detect state save on early exit** — already_applied and not-tailored cases skip `save_state`. Next `act` loads stale file.
22. **Act --fill/--next both passed** — first flag wins. Silent.
23. **Act --next after --submit dry-run** — user sees "NO_BUTTON" instead of clear guidance.
24. **Linkedin scraper title dedup threshold** — >20 chars. Some legit short titles could be duplicated and missed.
25. **Fetch --refresh not in SKILL.md help** — still referenced in SKILL.md but may not work as described.
26. **SKILL.md duplicate pre-flight table** — Quebec rule repeated in extraction rules and pre-flight.
27. **AGENTS.md gmail-cli path** — references `gmail-cli auth add` but path `skills/gmail-cli/gmail_cli.py` not specified.
28. **Call_gemini node_modules import-time check** — if installed after import, env not set until restart.
29. **Call_gemini temp file** — writes to system tempdir. Disk full fails silently.
30. **Verify.py no LinkedIn page** — external ATS submission has no LinkedIn page, always returns "unknown".

## Design-level (decisions with tradeoffs)

31. **Single apply_state.json** — running detect on job B while job A is in-progress overwrites A's state.
32. **No timeout per fill/next call** — hanging on an unresponsive form blocks the pipeline.
33. **Act --fill re-enters same page** — model can accidentally fill the same page twice. No guard.
34. **Multiple resumes in results dir** — no per-job isolation enforcement.
35. **No back-forward consistency check** — `act --back` returns to previous page but `act --fill` might fill different fields.
36. **Profile.json common_answers grows unbounded** — no cleanup mechanism.
37. **LinkedIn page URL format fragile** — `split("/jobs/view/")[1].split("/")[0]` breaks on URLs with trailing `/`.
38. **No input type="number" handling** — `act --fill` treats number inputs as text, may fail on some forms.
39. **Unfollow checkbox uses `change` event** — if page uses `input` event instead, uncheck doesn't register.
40. **Tailor --jid doesn't update in-memory stage** — `advance` updates DB but not the local `entry` dict for subsequent reads within the same function.
41. **Act --next disabled detection** — checks all buttons before giving up. But if "Submit" is the only disabled button and "Next" is enabled, it clicks "Next" correctly.
42. **Act --fill radio fallback** — uses `el.check()` which may dispatch events that React doesn't catch. Same as old radio.click() issue.
43. **No URL normalization** — URLs with and without trailing `/` are treated as different jobs.
44. **No job-age tracking** — old jobs from email digests may be expired but still show as "extracted".
45. **Fetch.py title overwrite** — re-fetching a job overwrites the existing title with the page `<title>` tag, which may be less specific.
46. **Act --answers case sensitivity** — exact normalized match is case-insensitive. Keys with different casing still match.
47. **Act --fill before detect** — if user runs `act --fill` without running `detect` first, state file doesn't exist or is from a different job. Fails clearly at JSON decode.
48. **No idle timeout on apply_state.json** — stale state files from crashed sessions never auto-cleaned.
49. **LinkedIn scraper page navigation waits 5s** — fixed wait, regardless of page load speed.
50. **Tailor.py generate_tailored_docs imports `desc_cleaners`** — module doesn't exist. Import fails silently at module level? Actually it's imported as `from lib.platforms import clean as clean_desc` — confirmed exists.
