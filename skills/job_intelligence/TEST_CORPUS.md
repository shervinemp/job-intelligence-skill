# Test Corpus — 200 full apply / message / auth / state cases

Each case: **scenario** (what the world looks like), **preconditions**
(state/DB/profile/page), **trace** (the code path, file:line), **expected**
(outcome + observable signal), **verify** (how to test / what to assert).
Reference docs: FUNCTIONAL_FLOW.md, CURVEBALLS.md, UNRECOVERABLE.md,
CAPTCHA_LOGIN_AUDIT.md, ADVERSARIAL2.md, FAILURE_MAP.md.

Status legend (fill in as you test): `UNTESTED` (default) / `PASS` / `FAIL` /
`FOUND` (bug confirmed).

---

## A. Ingestion & classification (extract → enrich → tailor)

### A-001. Gmail fetch with attachment — PASS-worthy
- **Scenario**: stage_emails.py pulls an email with a PDF job description attached.
- **Preconditions**: `~/.ji/state/jobs.db` empty or job unseen; email exists in gmail-cli search results.
- **Trace**: `stage_emails.py:44` (shlex query) → `extract.py` (attachment OCR/parse) → `lib/ingest.py`.
- **Expected**: job created with stage `described`, `source_url` populated, no duplicate when re-run.
- **Verify**: run twice; assert single job row, `extracted` flag set.

### A-002. Duplicate email re-fetch is idempotent
- **Scenario**: same email fetched again (search overlap).
- **Trace**: `extract.py` dedup on message-id / url.
- **Expected**: no second job row; existing job updated (not duplicated).
- **Verify**: assert `COUNT(*)` unchanged.

### A-003. LinkedIn job via linkedin.py entry point
- **Scenario**: `linkedin.py --max 3` pulls saved LinkedIn job posts.
- **Trace**: `linkedin.py:17` → `lib/ingest.py`.
- **Expected**: described jobs with `platform=linkedin`, `external_url` set.
- **Verify**: assert platform + external_url.

### A-004. enrich resolves company domain + name
- **Scenario**: described job with bare company name ("Lyft") → enrich.
- **Trace**: `enrich.py` → `lib/companies.py`.
- **Expected**: `company_domain`, `company_linkedin_slug` filled.
- **Verify**: assert enrich output JSON fields.

### A-005. enrich on unknown company is honest (no hallucinated domain)
- **Scenario**: company not in DB/known list.
- **Trace**: `lib/companies.py:4`.
- **Expected**: domain left empty / marked needs_data — never a guessed domain.
- **Verify**: assert no fabricated domain string.

### A-006. tailor generates tailored resume summary
- **Scenario**: described → tailor with profile.
- **Trace**: `tailor.py` → `lib/build_resume.py`.
- **Expected**: stage `tailored`, `resume_summary` present.
- **Verify**: assert stage + non-empty summary.

### A-007. tailor skips when profile incomplete
- **Scenario**: profile missing required fields.
- **Trace**: `lib/quality.py:17` (location missing check) → `tailor.py`.
- **Expected**: job NOT advanced; reason recorded.
- **Verify**: assert job stays `described` + quality reason.

### A-008. classifier route — role matching is data-driven
- **Scenario**: classification via default_answers.json / data-driven classifiers.
- **Trace**: `apply/common/resolve.py` + `lib/db/pipeline.py`.
- **Expected**: no hardcoded single-answer; profile-derived.
- **Verify**: flip profile field → different resolved answer.

### A-009. pronouns map is lookup not hardcode
- **Scenario**: pronoun resolution.
- **Trace**: `pronouns.json`, `apply/common/match.py`.
- **Expected**: covers common sets; unknown pronoun → safe default.
- **Verify**: assert unknown input doesn't crash.

### A-010. dialing-code map is data
- **Scenario**: country→dialing code.
- **Trace**: `apply/common/match.py:115` country_iso + dialing map.
- **Expected**: covers common countries; `country_iso("Atlantis")==''` (test_resolve).
- **Verify**: existing `test_country_iso`.

### A-011. alias rules drive field matching
- **Scenario**: "country of employment" → canonical `country`.
- **Trace**: `lib/db/fills.py` `_lookup_learned` + `add_alias_rule`.
- **Expected**: alias maps to canonical; unknown → no match (needs_data).
- **Verify**: `test_fill_ledger.py`.

### A-012. field-method learning is domain-scoped
- **Scenario**: "Phone country code" learned `combobox` on jobs.acme.com.
- **Trace**: `apply/common/field_methods.py` `record_method` / `prefer_method`.
- **Expected**: scoped to domain; other domains get no preference.
- **Verify**: `test_field_methods.py`.

### A-013. verification-strategy learning
- **Scenario**: per-field verify strategy recorded and preferred.
- **Trace**: `apply/common/field_methods.py`.
- **Expected**: returns learned strategy for that domain+field.
- **Verify**: `test_field_methods.py` (2-pref wins).

### A-014. adjudication `wrong` retracts learned mapping
- **Scenario**: human marks a fill `wrong`.
- **Trace**: `report.py` adjudicate → retract learned mapping + rule + profile-suspect + field-method.
- **Expected**: learned mapping removed; next run re-derives.
- **Verify**: `test_fill_ledger.py` (`_lookup_learned` None after retract).

### A-015. adjudication `ok` keeps learning
- **Scenario**: human marks `ok`.
- **Trace**: `report.py` adjudicate.
- **Expected**: learning retained, method reinforced.
- **Verify**: method still preferred.

### A-016. SPC (silent-poison counter) tripwire
- **Scenario**: too many silent wrong fills accumulate.
- **Trace**: `lib/quality.py` / report fleet health.
- **Expected**: fleet health score drops / tripwire flags.
- **Verify**: `report.py fleet`.

### A-017. ingest dedup by URL across sources
- **Scenario**: same job URL from LinkedIn AND email.
- **Trace**: `lib/db/jobs.py` upsert by url.
- **Expected**: one job; second source updates, not duplicates.
- **Verify**: assert single row.

### A-018. job expires / state=active filter
- **Scenario**: job marked closed/expired.
- **Trace**: `lib/db/jobs.py` state field; `cmd_discover_all` filters `state='active'`.
- **Expected**: expired jobs excluded from fleet scans.
- **Verify**: assert not in `ready`/discover output.

### A-019. extract structured fallback to LLM
- **Scenario**: attachment parse fails → ask_api text path.
- **Trace**: `lib/extract_structured.py`, `lib/ask_api.py`.
- **Expected**: falls back cleanly; no crash; `needs_data` if still nothing.
- **Verify**: stub ask_api unavailable → assert graceful.

### A-020. ask_api minimization holds (text outward)
- **Scenario**: orchestrator is strong model; ask_api only vision.
- **Trace**: `ji.py` routing, `lib/ask_api.py`.
- **Expected**: text evidence surfaces in dossier, not ask_api judgment.
- **Verify**: assert ask_api not called for text classification in auto mode.

---

## B. Apply — detect & navigate

### B-001. `detect` external URL route
- **Scenario**: job with external_url → `detect` returns `external`.
- **Trace**: `apply/act/helpers.py:_resolve_linkedin_apply` → detect branch.
- **Expected**: navigation follows external URL.
- **Verify**: CURVEBALL C3 (OK).

### B-002. `detect` easy-apply route
- **Scenario**: LinkedIn job, no external URL.
- **Trace**: detect → easy_apply.
- **Expected**: opens LinkedIn Easy Apply modal.
- **Verify**: assert mode= easy_apply.

### B-003. `navigate` follows redirect host change
- **Scenario**: external URL redirects to different host.
- **Trace**: `fill.py:396` REDIRECT check → updates `external_url`.
- **Expected**: state external_url updated to final host.
- **Verify**: assert state after run.

### B-004. landing page broken → url fallbacks
- **Scenario**: landing URL is an error page.
- **Trace**: `fill.py:401-414` `_is_error_page` + `_url_fallbacks`.
- **Expected**: tries alternates; updates external_url on success.
- **Verify**: fake error page → assert fallback attempted.

### B-005. cross-origin standalone form resolved
- **Scenario**: page links to a cross-origin form.
- **Trace**: `fill.py:416-419` `_resolve_standalone_form_url`.
- **Expected**: navigates to the real form; state updated.
- **Verify**: assert CROSS-ORIGIN FORM path taken.

### B-006. apply button click detection
- **Scenario**: "Apply" button present.
- **Trace**: `helpers.py:_click_apply_button`.
- **Expected**: clicks; proceeds to form.
- **Verify**: assert APPLY_CLICKED signal.

### B-007. no apply path — honest failure
- **Scenario**: no apply button / login-only page.
- **Trace**: `apply/auto.py:_no_apply_path_detail`.
- **Expected**: status `no_apply_path`; not marked applied.
- **Verify**: assert status in report.

### B-008. session-timeout popup dismissed
- **Scenario**: Workday "session about to time out" dialog.
- **Trace**: `page_helpers.py:12 handle_session_timeout`.
- **Expected**: "Keep Working" clicked; flow continues.
- **Verify**: mock dialog → assert clicked.

### B-009. cookie banner dismissed pre-login
- **Scenario**: cookie consent overlay blocks clicks.
- **Trace**: `fill.py:1013-1027`.
- **Expected**: Accept clicked once; fill proceeds.
- **Verify**: assert no click-interception.

### B-010. dialogs wired / alert auto-dismiss
- **Scenario**: native alert() during interaction.
- **Trace**: `helpers.py:_wire_dialogs`.
- **Expected**: dialog auto-handled; no hang.
- **Verify**: test_corpus dialog case.

### B-011. tag_page binds jid to page
- **Scenario**: multiple tabs open; find correct page.
- **Trace**: `page_helpers.py:33 tag_page`, `find_page:277`.
- **Expected**: page found by tag before URL scoring.
- **Verify**: assert correct page selected.

### B-012. find_page URL scoring
- **Scenario**: no tag match; multiple candidate tabs.
- **Trace**: `page_helpers.py:286-302` score exact/prefix.
- **Expected**: best-scored page wins.
- **Verify**: assert chosen page.

### B-013. cross-origin redirect is one-shot-safe
- **Scenario**: navigate to external, redirect to ATS.
- **Trace**: `fill.py:396` → updates external_url.
- **Expected**: submit guard still applies to final host.
- **Verify**: assert submit gate not bypassed by redirect.

### B-014. apply button inside dialog scoped
- **Scenario**: apply button only inside `[role=dialog]`.
- **Trace**: `page_state.py:find_buttons` + dialog scope.
- **Expected**: button found in dialog scope.
- **Verify**: assert candidate has `_inDialog`.

### B-015. registry guest-apply pattern
- **Scenario**: Workday "Apply as guest".
- **Trace**: `fill.py:978-990` registry guest_apply patterns.
- **Expected**: guest apply clicked → fill continues.
- **Verify**: assert GUEST_APPLY signal.

### B-016. registry custom widget selectors
- **Scenario**: ATS with custom widget (Ashby autocomplete).
- **Trace**: `page_helpers.py:241 read_page` registry widgets.
- **Expected**: widget treated as custom; not native select.
- **Verify**: assert field_role=combobox.

### B-017. registry per-domain override wins
- **Scenario**: domain registry defines widget parent.
- **Trace**: `apply/common/registry.py`.
- **Expected**: custom_widgets["parent"] used in read.
- **Verify**: assert parent selector applied.

### B-018. probe captures field roles
- **Scenario**: probe a form → roles standard/combobox/select.
- **Trace**: `apply/common/capabilities.py`, `helpers.py:_probe_form`.
- **Expected**: field_role populated per field.
- **Verify**: assert roles on probe result.

### B-019. probe detects iframes → merged fields
- **Scenario**: form fields inside iframe.
- **Trace**: `page_helpers.py:264-273 probe_iframes` merge.
- **Expected**: iframe fields appended when more than page fields.
- **Verify**: assert merged count.

### B-020. progress bar → multi-page known
- **Scenario**: `[role=progressbar]` present.
- **Trace**: `capabilities.py:177-182`.
- **Expected**: `has_progress_bar=True`; orchestrator knows multi-step.
- **Verify**: assert capability flag.

---

## C. Apply — read & probe

### C-001. read_fields on document scope
- **Scenario**: simple form, no dialog.
- **Trace**: `field_reader.py:read_fields` scope=document.
- **Expected**: all visible inputs/selects/textareas captured.
- **Verify**: assert fieldCount>0.

### C-002. read_fields dialog scope fallback
- **Scenario**: fields only in a modal dialog.
- **Trace**: `page_helpers.py:256-263` — dialog scope then document fallback.
- **Expected**: dialog fields captured (even if document saw 0).
- **Verify**: assert _scoped_to=dialog.

### C-003. field label normalization (accents/case)
- **Scenario**: "Email" vs "email" vs "E-mail".
- **Trace**: `apply/common/match.py` normalize.
- **Expected**: canonical match.
- **Verify**: `test_resolve.py` normalize cases.

### C-004. junk-field filtering
- **Scenario**: hidden/honeypot/search inputs.
- **Trace**: `helpers.py:_is_junk_field`.
- **Expected**: junk excluded from fill set.
- **Verify**: assert not in fields.

### C-005. honeypot detection
- **Scenario**: hidden "website" robots field.
- **Trace**: `capabilities.py:194-205`.
- **Expected**: honeypot count>0 → cautious fill.
- **Verify**: assert honeypot_signals.

### C-006. required-field detection
- **Scenario**: `required` attributes + asterisk labels.
- **Trace**: `field_reader.py` / shapes.
- **Expected**: `required=True` on those fields.
- **Verify**: assert required flags.

### C-007. value read-back after fill (native select)
- **Scenario**: SELECT filled → read selected option text.
- **Trace**: `value_reader.py:51` el.options[selectedIndex].
- **Expected**: returns option text, not raw value.
- **Verify**: `test_corpus.py` select read-back.

### C-008. value read-back custom widget
- **Scenario**: react-select/combobox.
- **Trace**: `value_reader.py` custom branch.
- **Expected**: reads aria-selected or single-value text.
- **Verify**: `helpers.py:363-383` scope logic.

### C-009. selected-state trap (combobox pre-filled)
- **Scenario**: combobox already has a value → skip fill.
- **Trace**: `helpers.py:378` `.select__single-value` continue.
- **Expected**: field skipped as already filled; verified not overwritten.
- **Verify**: assert skip + kept value.

### C-010. aria-selected already chosen trap
- **Scenario**: option already `aria-selected=true`.
- **Trace**: `helpers.py:383`.
- **Expected**: skip (already set).
- **Verify**: assert not clobbered.

### C-011. field profile probe (options list)
- **Scenario**: native select probed for options.
- **Trace**: `field_reader.py:144` first 15 option texts.
- **Expected**: options captured (may be lazy partial).
- **Verify**: assert options list.

### C-012. field type detection (datepicker)
- **Scenario**: input[type=date].
- **Trace**: `field_reader.py:184`.
- **Expected**: type=native date.
- **Verify**: assert field type.

### C-013. field type detection (contenteditable)
- **Scenario**: div[contenteditable] rich text.
- **Trace**: `strategies/contenteditable.py`.
- **Expected**: handled by contenteditable strategy.
- **Verify**: assert strategy chosen.

### C-014. multi-iframe field dedup
- **Scenario**: same label in page + iframe.
- **Trace**: `page_helpers.py:268-273` existing-set dedup.
- **Expected**: no duplicate field entries.
- **Verify**: assert unique labels.

### C-015. shadow-DOM text included in page_text
- **Scenario**: text inside shadow root.
- **Trace**: `page_helpers.py:104 page_text` `:defined` walk.
- **Expected**: shadow text present (feeds captcha/success detection).
- **Verify**: assert included.

---

## D. Apply — fill strategies

### D-001. standard text input — fill method
- **Scenario**: plain `<input type=text>`.
- **Trace**: `strategies/text.py:METHOD_CHAIN` first `fill`.
- **Expected**: filled, read-back matches.
- **Verify**: assert fill method + read-back.

### D-002. native_setter fallback for controlled inputs
- **Scenario**: React-controlled input rejects `.fill` clear.
- **Trace**: `strategies/text.py:native_setter` (el.value=).
- **Expected**: value set; change/input dispatched.
- **Verify**: `test_history_and_batch.py:289` method=native_setter.

### D-003. autocomplete typeahead
- **Scenario**: typeahead needs keyboard events.
- **Trace**: `strategies/text.py:autocomplete` after `fill("")`.
- **Expected**: typed; suggestions may appear.
- **Verify**: assert autocomplete method used.

### D-004. dispatch_events final fallback
- **Scenario**: value set but React not notified.
- **Trace**: `strategies/text.py:dispatch_events`.
- **Expected**: input+change events fired.
- **Verify**: assert events dispatched.

### D-005. select_option method (native select)
- **Scenario**: native SELECT with value==text options.
- **Trace**: `strategies/select.py:146` select_option.
- **Expected**: option selected.
- **Verify**: `NativeSelectStrategy.test_select_option_method_still_works`.

### D-006. native_setter index-based select (CURVEBALL B)
- **Scenario**: value='CA', text='Canada (+1)', lazy DOM.
- **Trace**: `strategies/select.py:_set_native_by_index` via el.options.
- **Expected**: selectedIndex set; bypasses truncated DOM.
- **Verify**: `NativeSelectStrategy.test_value_differs_from_text_selects_by_index`.

### D-007. js_click select fallback
- **Scenario**: no other select method works.
- **Trace**: `strategies/select.py:155` js_click (el.selectedIndex).
- **Expected**: sets selectedIndex of matched option.
- **Verify**: assert selectedIndex set.

### D-008. country-name match beats bare code (Antigua)
- **Scenario**: answer '+1', country_words=['canada'], option 'Canada (+1)' present.
- **Trace**: `strategies/select.py:_pick_option` country-match step 2.
- **Expected**: picks Canada, never Antigua.
- **Verify**: `test_verify_redteam.py`.

### D-009. known-country-not-loaded → no match (Antigua guard)
- **Scenario**: '+1', country_words=['canada'], Canada absent from options.
- **Trace**: `strategies/select.py:49-51` returns None before bare-code.
- **Expected**: no confident pick → orchestrator/vision; never Antigua.
- **Verify**: `NativeSelectStrategy.test_bare_code_known_country_not_loaded_is_no_match`.

### D-010. no country context → bare-code fallback allowed
- **Scenario**: '+1', no country_words.
- **Trace**: `strategies/select.py:56-61`.
- **Expected**: falls to paren match (accepts risk: no better info).
- **Verify**: `test_bare_code_falls_to_bare_code`.

### D-011. combobox strategy chain
- **Scenario**: custom combobox field.
- **Trace**: `strategies/combobox.py:20 defs`.
- **Expected**: clicks → types → picks option.
- **Verify**: assert combobox method used.

### D-012. datepicker fill
- **Scenario**: input[type=date].
- **Trace**: `strategies/datepicker.py`.
- **Expected**: ISO date filled.
- **Verify**: assert value format.

### D-013. contenteditable fill
- **Scenario**: rich-text answer.
- **Trace**: `strategies/contenteditable.py`.
- **Expected**: text set, events dispatched.
- **Verify**: assert content.

### D-014. phone country resolves to country, not bare code
- **Scenario**: field "Phone country code", answer +1 with location Canada.
- **Trace**: `apply/common/resolve.py` + `test_verify_redteam`.
- **Expected**: resolves to CANADA.
- **Verify**: `test_phone_country_code_resolves_to_country`.

### D-015. phone country with no country → needs_data
- **Scenario**: no location/country in profile.
- **Trace**: resolve → needs_data.
- **Expected**: never a guessed dialing code.
- **Verify**: `test_phone_country_code_no_country_is_no_match`.

### D-016. tel autocomplete country code
- **Scenario**: autocomplete=tel-country-code.
- **Trace**: resolve autocomplete branch.
- **Expected**: country selected; not Antigua.
- **Verify**: `test_tel_country_code_autocomplete_resolves_to_country`.

### D-017. postal code not used as country
- **Scenario**: location "Toronto, ON, M5V 2T6".
- **Trace**: `apply/common/resolve.py` ephemeral split.
- **Expected**: country != 'M5V 2T6'.
- **Verify**: `test_postal_code_not_used_as_country`.

### D-018. postal suffix stripped from country
- **Scenario**: "Quebec City, QC, Canada G1R 5J4".
- **Trace**: resolve country extraction.
- **Expected**: country 'Canada'.
- **Verify**: `test_postal_suffix_stripped_from_country`.

### D-019. conditional-reveal sweep clicks
- **Scenario**: radio/yesno reveals more fields.
- **Trace**: `fill.py:818-828` conditional-reveal sweep.
- **Expected**: revealed fields then filled.
- **Verify**: assert revealed field count grows.

### D-020. required-unanswered surfaced
- **Scenario**: required field with no answer.
- **Trace**: `fill.py:921-924` req_no_answer.
- **Expected**: status lists REQUIRED unanswered; not "filled".
- **Verify**: assert REQUIRED in status.

### D-021. field already-filled skip (submit_visible fast path)
- **Scenario**: form mostly pre-filled.
- **Trace**: `fill.py:926` submit_visible → next check.
- **Expected**: skips fill; routes to check.
- **Verify**: assert emit_next check.

### D-022. zero fields → inspect path
- **Scenario**: no fillable fields found.
- **Trace**: `fill.py:928-929`.
- **Expected**: routes to act --inspect; not applied.
- **Verify**: assert emit_next inspect.

### D-023. radio group selection
- **Scenario**: yes/no radio, expected value.
- **Trace**: `check.py:208-210` radio selected read; fill radio path.
- **Expected**: correct option selected.
- **Verify**: `test_corpus.py` radio case.

### D-024. cross-field country/location coherence
- **Scenario**: location filled but country mismatch.
- **Trace**: `check.py:381-388` cross-field check.
- **Expected**: flagged as issue (country vs location).
- **Verify**: assert flag label.

### D-025. sponsorship question answered from profile
- **Scenario**: "Will you require sponsorship?"
- **Trace**: resolve → profile-derived.
- **Expected**: correct yes/no.
- **Verify**: assert answer matches profile.

---

## E. Apply — check & validate

### E-001. check validates filled values
- **Scenario**: run `act --check` after fill.
- **Trace**: `apply/act/check.py:cmd_check`.
- **Expected**: compares filled vs expected; reports diffs.
- **Verify**: assert issue list.

### E-002. wrong country flagged by check
- **Scenario**: filled country ≠ profile country.
- **Trace**: `check.py` country compare.
- **Expected**: issue "country mismatch".
- **Verify**: assert issue label.

### E-003. react-widget detection in check
- **Scenario**: field is react-select; normal compare would false-flag.
- **Trace**: `check.py:_is_react_widget`.
- **Expected**: uses widget-aware compare.
- **Verify**: assert not false-flagged.

### E-004. normalization for compare
- **Scenario**: "Toronto" vs "toronto" vs whitespace.
- **Trace**: `check.py:_normalize_for_compare`.
- **Expected**: equal after normalize.
- **Verify**: assert no spurious diff.

### E-005. emit_next_value surfaces next action
- **Scenario**: check passes → next action.
- **Trace**: `check.py:emit_next_value`.
- **Expected**: next = submit/verify.
- **Verify**: assert emit.

### E-006. check on already-applied job short-circuits
- **Scenario**: stage=applied.
- **Trace**: `check.py:79`.
- **Expected**: early return; no browser.
- **Verify**: assert no page session.

### E-007. verify preconditions (post-submit)
- **Scenario**: job filled, pending submit verify.
- **Trace**: `apply/verify.py:run`.
- **Expected**: checks success signals before declaring applied.
- **Verify**: assert applied only on signal.

### E-008. confirmation-URL detection
- **Scenario**: URL `/application/complete`.
- **Trace**: `verify.py:_is_confirmation_url`.
- **Expected**: recognized as success.
- **Verify**: assert true.

### E-009. registrable-domain check
- **Scenario**: fake/typo success page (attacker-controlled).
- **Trace**: `verify.py:_registrable_domain`.
- **Expected**: non-registrable / wrong ATS not trusted.
- **Verify**: assert rejection.

### E-010. vision confirm as second observer
- **Scenario**: text ambiguous → vision API screenshot.
- **Trace**: `verify.py:_vision_confirms`.
- **Expected**: vision verdict weighs; not sole authority.
- **Verify**: assert called only when ambiguous.

---

## F. Apply — next / inspect / investigate

### F-001. --next on mid-form page clicks Next (CURVEBALL C1 safe path)
- **Scenario**: multi-step form, "Next" button (not submit-like).
- **Trace**: `inspect.py:cmd_next` → `_find_next_button` → `_click_action`.
- **Expected**: clicked; emit_next fill.
- **Verify**: `NextButtonSubmitGate.test_plain_next_clicked`.

### F-002. --next refuses submit-like button (C1 FIXED)
- **Scenario**: final button "Continue to Review".
- **Trace**: `inspect.py:cmd_next` BUTTON_GATE → refuses, routes to submit.
- **Expected**: NOT clicked; emit_next act --submit.
- **Verify**: `NextButtonSubmitGate.test_submit_like_button_refused`.

### F-003. --next with submit_clicked refuses (C2 FIXED)
- **Scenario**: submit was clicked (uncertain) → --next.
- **Trace**: `inspect.py:cmd_next` GUARD reads submit_clicked.
- **Expected**: refuses; routes to investigation.
- **Verify**: `NextButtonSubmitGate.test_submit_clicked_refuses_next`.

### F-004. --next no button found → error
- **Scenario**: no next/continue button.
- **Trace**: `inspect.py` emit_error.
- **Expected**: returns 1, no click.
- **Verify**: assert error.

### F-005. inspect dumps page evidence
- **Scenario**: act --inspect on a stuck page.
- **Trace**: `apply/act/investigate.py`, `apply/act/inspect.py`.
- **Expected**: HTML dump + screenshot + state captured.
- **Verify**: assert dump files exist.

### F-006. investigate CAPTCHA wait
- **Scenario**: investigate finds captcha.
- **Trace**: `investigate.py:24 handle_captcha`.
- **Expected**: waits human solve; records captcha_required on timeout.
- **Verify**: assert status.

### F-007. `_find_next_button` keyword scoring
- **Scenario**: multiple candidates ("Next", "Next > Step 2").
- **Trace**: `helpers.py:_find_next_button` score text match.
- **Expected**: highest score wins.
- **Verify**: assert chosen text.

### F-008. `_click_action` scrolls into view
- **Scenario**: button below fold.
- **Trace**: `helpers.py:_click_action`.
- **Expected**: scrolls then clicks.
- **Verify**: assert scroll called.

### F-009. button disabled → not clicked
- **Scenario**: next button disabled until field filled.
- **Trace**: `scan_actions` disabled flag; `_find_next_button`.
- **Expected**: not clicked; reported.
- **Verify**: assert disabled handling.

### F-010. next click causes navigation → state updated
- **Scenario**: multi-page ATS advances step.
- **Trace**: `inspect.py` after click sleep + emit fill.
- **Expected**: new step fills.
- **Verify**: assert new fields appear.

---

## G. Apply — submit (one-shot safety)

### G-001. submit gate blocks duplicate submit (submit_clicked)
- **Scenario**: submit_clicked already set.
- **Trace**: `submit.py:cmd_submit` guard.
- **Expected**: refuses; no second click.
- **Verify**: assert guard message.

### G-002. submit success → applied + applied_at
- **Scenario**: valid submit, success signal.
- **Trace**: `submit.py:_determine_outcome` → `mark_applied`.
- **Expected**: stage=applied, applied_at set, submit_clicked cleared.
- **Verify**: assert DB row.

### G-003. mark_applied atomic (was_new)
- **Scenario**: two processes race past stage check.
- **Trace**: `page_helpers.py:41 mark_applied` WHERE stage != 'applied'.
- **Expected**: exactly one transition.
- **Verify**: assert rowcount logic.

### G-004. submit on-target counts as success (target_url)
- **Scenario**: applied-text appears on the TARGET url.
- **Trace**: `submit.py:_determine_outcome(target_url=url)`.
- **Expected**: success recognized.
- **Verify**: `AlreadyAppliedTargetGuard` test.

### G-005. non-target applied-text NOT success (FIXED)
- **Scenario**: already-applied text on a different page (e.g. another tab).
- **Trace**: `submit.py:_determine_outcome` target_url mismatch.
- **Expected**: NOT counted; job not wrongly applied.
- **Verify**: `AlreadyAppliedTargetGuard` test (MongoDB/Dialpad recovery).

### G-006. validation errors → not applied
- **Scenario**: form rejects with field errors.
- **Trace**: `submit.py` validation_error outcome.
- **Expected**: status validation_error; job stays filled.
- **Verify**: assert status.

### G-007. captcha at submit → captcha_required
- **Scenario**: captcha present at submit step.
- **Trace**: `submit.py:362 handle_captcha`.
- **Expected**: waits human; records captcha_required.
- **Verify**: assert status.

### G-008. post-submit confirmation modal detected
- **Scenario**: dialog says "Application submitted".
- **Trace**: `capabilities.py:238-239 successModalText`.
- **Expected**: success recorded.
- **Verify**: assert success modal flag.

### G-009. uncertain submit → never re-click, investigate
- **Scenario**: outcome unclear after click.
- **Trace**: `submit.py` uncertain → `investigate`.
- **Expected**: no second click; user investigates.
- **Verify**: assert no duplicate.

### G-010. submit while already applied (stage check)
- **Scenario**: stage=applied → submit.
- **Trace**: `submit.py:190` stage check.
- **Expected**: early return.
- **Verify**: assert no browser.

### G-011. duplicate URL submit blocked (dup check)
- **Scenario**: same url applied on another job.
- **Trace**: `submit.py:271-282` URL dup query.
- **Expected**: flagged; no double apply.
- **Verify**: assert warning.

### G-012. form-still-present heuristic
- **Scenario**: after click, form remains → not success.
- **Trace**: `submit.py:_form_still_present`.
- **Expected**: outcome not applied.
- **Verify**: assert false.

### G-013. submit forces via --force only
- **Scenario**: operator re-runs submit with --force.
- **Trace**: `submit.py` force bypass.
- **Expected**: allowed; records.
- **Verify**: assert force path.

### G-014. submit undo clears flag
- **Scenario**: `undo` resets submit_clicked.
- **Trace**: `apply.py undo` → `clear_runtime_state`/`mark`.
- **Expected**: re-run possible.
- **Verify**: assert flag cleared.

### G-015. applied job survives state clear
- **Scenario**: apply_state.json cleared for applied job.
- **Trace**: `page_helpers.py:mark_applied` guard reset.
- **Expected**: applied not lost; stage is DB truth.
- **Verify**: assert DB applied.

---

## H. Apply — auto / shadow / batch / retry

### H-001. shadow run fills + checks WITHOUT submitting
- **Scenario**: `apply.py shadow` on tailored jobs.
- **Trace**: `apply.py` shadow workers.
- **Expected**: fills+checks; no submit; log `shadow_run.jsonl`.
- **Verify**: assert no applied jobs.

### H-002. shadow resumable log
- **Scenario**: shadow interrupted → resume.
- **Trace**: `~/.ji/state/shadow_run.jsonl`.
- **Expected**: skips done; continues.
- **Verify**: assert log entries.

### H-003. preflight gates fleet (profile completeness)
- **Scenario**: `apply.py preflight`.
- **Trace**: `apply.py preflight` profile gate.
- **Expected**: reports incomplete profiles before fleet.
- **Verify**: assert gate message.

### H-004. LLM retry fill after failure
- **Scenario**: fill failed; retry with LLM.
- **Trace**: `apply/auto.py:_retry_fill_with_llm`.
- **Expected**: retries within limits.
- **Verify**: assert retry bounded.

### H-005. LLM retry submit after validation error
- **Scenario**: submit validation error → LLM retry.
- **Trace**: `apply/auto.py:_retry_submit_with_llm`.
- **Expected**: retries; respects one-shot.
- **Verify**: assert no double submit.

### H-006. batch `--limit` honored
- **Scenario**: `apply.py --limit 3`.
- **Trace**: `apply.py` main.
- **Expected**: processes ≤3.
- **Verify**: assert count.

### H-007. `--quick` merges no-verify
- **Scenario**: quick mode skips post-submit verify.
- **Trace**: `apply.py --quick`.
- **Expected**: faster; risk noted.
- **Verify**: assert verify skipped.

### H-008. retry after FAILED
- **Scenario**: job FAILED → `apply.py retry`.
- **Trace**: `apply.py retry` / `reach.py cmd_retry`.
- **Expected**: re-processes.
- **Verify**: assert stage change.

### H-009. retry-skipped re-examines skips
- **Scenario**: SKIPPED → `retry-skipped`.
- **Trace**: `apply.py` retry-skipped.
- **Expected**: re-examines skip reasons.
- **Verify**: assert skipped jobs considered.

### H-010. shadow `--recheck` unconfirmed-skip queue
- **Scenario**: shadow --recheck.
- **Trace**: `apply.py shadow --recheck`.
- **Expected**: re-examines unconfirmed skips.
- **Verify**: assert queue scanned.

### H-011. error labels extracted from validation text
- **Scenario**: "Missing entry for required field: X".
- **Trace**: `apply/auto.py:_extract_error_labels`.
- **Expected**: labels parsed.
- **Verify**: `test_auto.py:161`.

### H-012. fleet scan health summary
- **Scenario**: `ji fleet-scan`.
- **Trace**: `ji.py` + `report.py`.
- **Expected**: clean output; no suspects.
- **Verify**: assert 0 suspects.

### H-013. `--job-id` filters batch
- **Scenario**: batch with --jid.
- **Trace**: `apply.py --jid`.
- **Expected**: only that job processed.
- **Verify**: assert subset.

---

## I. Auth — login / signup / captcha / 2FA

### I-001. no login wall → fill proceeds
- **Scenario**: page has no password field.
- **Trace**: `fill.py:_LOGIN_JS` → None.
- **Expected**: return "" continue.
- **Verify**: `test_no_password_field_returns_empty`.

### I-002. login wall with approved creds → auto-login
- **Scenario**: sign-in form, domain approved, creds exist.
- **Trace**: `fill.py:992-1066`.
- **Expected**: password filled; logged in.
- **Verify**: `test_login_success_returns_empty`.

### I-003. unapproved domain refuses creds (guard)
- **Scenario**: domain never authenticated before.
- **Trace**: `fill.py:1001 _domain_approved` CRED_GUARD.
- **Expected**: refuses to type password.
- **Verify**: `test_unapproved_domain_refuses_creds`.

### I-004. alt password promoted to primary
- **Scenario**: first password fails, second works.
- **Trace**: `fill.py:1057-1066` promotion.
- **Expected**: winner → primary, others alternates.
- **Verify**: `test_alt_password_promoted_to_primary`.

### I-005. 2FA required → surface, not success
- **Scenario**: login accepted, 2FA prompt.
- **Trace**: `fill.py:1067-1086`.
- **Expected**: status 2fa_required; no more password tries.
- **Verify**: `test_2fa_required_status`.

### I-006. all passwords fail → login_failed
- **Scenario**: every candidate rejected.
- **Trace**: `fill.py:1104-1107`.
- **Expected**: status login_failed.
- **Verify**: `test_all_passwords_fail_status`.

### I-007. login CAPTCHA → captcha_required (C-C2 FIXED)
- **Scenario**: reCAPTCHA on sign-in form.
- **Trace**: `fill.py` `_login_check`→"captcha" → caller.
- **Expected**: captcha_required; no creds saved/promoted.
- **Verify**: `LoginWallCaptcha.test_login_captcha_returns_captcha_required_and_never_promotes`.

### I-008. uncertain-then-captcha NOT assumed OK (C-C2 FIXED)
- **Scenario**: first check uncertain, re-check captcha.
- **Trace**: `fill.py:1087-1101` re-check handles "captcha".
- **Expected**: captcha_required; not assume-OK.
- **Verify**: `LoginWallCaptcha.test_uncertain_then_captcha_is_not_assumed_ok`.

### I-009. `_login_check` surfaces captcha from widget (C-C1 FIXED)
- **Scenario**: unresolved form + visible captcha widget.
- **Trace**: `fill.py:_login_check` + `check_captcha`.
- **Expected**: returns "captcha".
- **Verify**: `LoginWallCaptcha.test_login_check_surfaces_captcha_when_widget_present`.

### I-010. runtime detector sees reCAPTCHA iframe (C-C1 FIXED)
- **Scenario**: `iframe[src*=recaptcha]` visible.
- **Trace**: `page_helpers.py:check_captcha`.
- **Expected**: detected True.
- **Verify**: `LoginWallCaptcha.test_check_captcha_detects_recaptcha_iframe`.

### I-011. account creation success → creds saved
- **Scenario**: create form filled, success signal.
- **Trace**: `fill.py:1188-1219`.
- **Expected**: creds saved + shared pool.
- **Verify**: assert save_creds called.

### I-012. account creation CAPTCHA → no creds saved (C-C2 FIXED)
- **Scenario**: create form blocked by captcha.
- **Trace**: `fill.py:_check_account_created`→"captcha" → caller.
- **Expected**: captcha_required; password NOT written to disk.
- **Verify**: assert save_creds NOT called.

### I-013. account exists → try known password pool
- **Scenario**: email already registered.
- **Trace**: `fill.py:1220-1242` ACCOUNT_EXISTS.
- **Expected**: sign-in with pool attempted.
- **Verify**: assert sign-in loop.

### I-014. account creation validation rejected
- **Scenario**: weak password / mismatch.
- **Trace**: `fill.py:1244-1252` CREATE_FAIL.
- **Expected**: status login_required; validation errors shown.
- **Verify**: assert error print.

### I-015. generated password complexity
- **Scenario**: no shared password fits platform rules.
- **Trace**: `lib/credentials.py:gen_password_for_platform`.
- **Expected**: compliant password generated.
- **Verify**: assert length/rules.

### I-016. cookie banner dismissed before login
- **Scenario**: Accept Cookies overlay.
- **Trace**: `fill.py:1013-1027`.
- **Expected**: accepted; login proceeds.
- **Verify**: assert click.

### I-017. Workday create→sign-in form switch
- **Scenario**: default Create Account with Sign In link.
- **Trace**: `fill.py:1033-1046`.
- **Expected**: switches to sign-in; fills creds.
- **Verify**: assert signInLink click.

### I-018. login "uncertain" → extended wait → OK
- **Scenario**: SPA slow transition.
- **Trace**: `fill.py:1087-1101`.
- **Expected**: waits, re-checks once.
- **Verify**: assert re-check.

### I-019. `_login_check` "no" on error text
- **Scenario**: "Incorrect password" text.
- **Trace**: `fill.py:_login_check` errors regex.
- **Expected**: "no".
- **Verify**: assert.

### I-020. `_login_check` 2FA input detection
- **Scenario**: `input[autocomplete=one-time-code]`.
- **Trace**: `fill.py:_login_check` 2FA branch.
- **Expected**: "2fa".
- **Verify**: assert.

---

## J. Reach — discover & list

### J-001. discover finds recruiters/team/connections
- **Scenario**: `reach.py discover <jid>`.
- **Trace**: `reach.py:cmd_discover` → `discover_contacts`.
- **Expected**: contacts stored; contact_discovered=1.
- **Verify**: assert contacts rows.

### J-002. discover --team names team members
- **Scenario**: `reach.py discover --team "X"`.
- **Trace**: `reach.py:cmd_discover(team_name=...)`.
- **Expected**: team contacts added.
- **Verify**: assert team sources.

### J-003. discover --all iterates jobs
- **Scenario**: `discover --all`.
- **Trace**: `reach.py:cmd_discover_all` (stage described/tailored, active).
- **Expected**: each job discovered.
- **Verify**: assert NO_JOBS_TO_DISCOVER when none.

### J-004. discover --limit honored
- **Scenario**: `discover --all --limit 2`.
- **Trace**: `reach.py:173-174`.
- **Expected**: ≤2 jobs.
- **Verify**: assert count.

### J-005. contact list prints DB order (index match)
- **Scenario**: `reach.py list <jid>`.
- **Trace**: `reach.py:cmd_list` enumerate 1..n.
- **Expected**: index matches `--contact N`.
- **Verify**: assert index alignment.

### J-006. discover error → message, no crash
- **Scenario**: browser/LLM fail.
- **Trace**: `reach.py:132-136`.
- **Expected**: ERROR printed; returns.
- **Verify**: assert error message.

### J-007. re-discover reset flag
- **Scenario**: `reach.py retry-discovery` (implied).
- **Trace**: `reach.py:660-664` resets contact_discovered.
- **Expected**: re-runs discover.
- **Verify**: assert flag reset.

### J-008. email candidates suggested
- **Scenario**: discover returns email pattern candidates.
- **Trace**: `reach.py:153-157`.
- **Expected**: printed with confidence.
- **Verify**: assert output.

### J-009. blank identity surfaces in list
- **Scenario**: contact with no email/url.
- **Trace**: `reach.py:cmd_list`.
- **Expected**: shows blank; guard warns later.
- **Verify**: assert row present.

---

## K. Reach — email

### K-001. email send success → flag + attempt + event
- **Scenario**: `reach.py email <jid>` succeeds.
- **Trace**: `reach.py:307-318`.
- **Expected**: EMAIL_SENT, email_sent=1, attempt sent, event.
- **Verify**: `test_email_records_attempt_flag_and_event`.

### K-002. email refused under test sandbox (JI_TESTS)
- **Scenario**: test env sends email.
- **Trace**: `reach.py:_sandbox_refused` + gmail-cli JI_TESTS guard.
- **Expected**: TEST_SANDBOX; no transmission.
- **Verify**: `test_email_refused_by_sandbox`.

### K-003. email already sent → blocked (flag)
- **Scenario**: email_sent=1, no force.
- **Trace**: `reach.py:236`.
- **Expected**: blocked; use --force.
- **Verify**: assert message.

### K-004. email cross-job duplicate → ALREADY_REACHED
- **Scenario**: same person reached on another job.
- **Trace**: `reach.py:_prior_outreach` → `_block_if_prior`.
- **Expected**: blocked.
- **Verify**: `test_email_blocked_cross_job`.

### K-005. email failed → attempt recorded (not silent)
- **Scenario**: gmail-cli returns error.
- **Trace**: `reach.py:301-305, 321-327`.
- **Expected**: EMAIL_FAILED; attempt failed recorded; email_sent=0.
- **Verify**: `test_failed_email_still_records_the_attempt`.

### K-006. email timeout → failed attempt
- **Scenario**: subprocess timeout.
- **Trace**: `reach.py:328-331`.
- **Expected**: EMAIL_FAILED timeout; attempt recorded.
- **Verify**: assert.

### K-007. gmail-cli missing → FileNotFoundError handled
- **Scenario**: gmail-cli not installed.
- **Trace**: `reach.py:332-334`.
- **Expected**: attempt failed recorded; message.
- **Verify**: assert.

### K-008. email --dry-run sends nothing
- **Scenario**: `reach.py email --dry-run`.
- **Trace**: `reach.py:cmd_email(dry_run=True)`.
- **Expected**: no transmission, no flags.
- **Verify**: assert no attempt row.

### K-009. email with body file
- **Scenario**: `--body-file`.
- **Trace**: `reach.py:cmd_email` body_file.
- **Expected**: body loaded from file.
- **Verify**: assert body used.

### K-010. email validation error (bad recipient)
- **Scenario**: malformed email address.
- **Trace**: `reach.py` validation.
- **Expected**: refused before send.
- **Verify**: assert.

### K-011. email BLANK_IDENTITY warning (C7)
- **Scenario**: contact has no email or url.
- **Trace**: `reach.py:_block_if_prior` blank warning.
- **Expected**: warning, not blocked (email needs addr though).
- **Verify**: `test_blank_identity_warns_but_does_not_block`.

### K-012. email uncertain outcome handling
- **Scenario**: send uncertain → verify in inbox then --set-sent.
- **Trace**: `reach.py` uncertain path.
- **Expected**: never silent resend; --set-sent settles.
- **Verify**: assert hint printed.

---

## L. Reach — message (LinkedIn DM)

### L-001. message success → flag + attempt + event
- **Scenario**: DM sent.
- **Trace**: `reach.py:427-434`.
- **Expected**: MESSAGE_SENT, message_sent=1, attempt sent.
- **Verify**: `test_linkedin_message_records_attempt_flag_and_event`.

### L-002. message refused under sandbox
- **Scenario**: test env.
- **Trace**: `reach.py:_sandbox_refused` + `lib/linkedin_messaging.py`.
- **Expected**: TEST_SANDBOX; browser not opened.
- **Verify**: `test_message_refused_by_sandbox_before_opening_a_browser`.

### L-003. deep sandbox in messaging module
- **Scenario**: direct send_message() call under JI_TESTS.
- **Trace**: `lib/linkedin_messaging.py` send_message.
- **Expected**: status sandbox_refused.
- **Verify**: `test_deep_sandbox_in_messaging_module`.

### L-004. message blocked cross-job
- **Scenario**: same person reached on other job.
- **Trace**: `reach.py:_prior_outreach`.
- **Expected**: ALREADY_REACHED.
- **Verify**: `test_message_blocked_when_person_reached_on_other_job`.

### L-005. message blocked via uncertain attempt
- **Scenario**: pending attempt on other job.
- **Trace**: `reach.py:_prior_outreach` status pending.
- **Expected**: blocked.
- **Verify**: `test_message_blocked_via_uncertain_attempt`.

### L-006. connect→DM same-row funnel allowed
- **Scenario**: connect sent, now DM same person+job.
- **Trace**: `reach.py:_prior_outreach` excludes current row.
- **Expected**: allowed (intended funnel).
- **Verify**: `test_connect_then_message_same_row_funnel_allowed`.

### L-007. duplicate row same job blocked
- **Scenario**: same person twice in one job's contacts.
- **Trace**: `reach.py:_prior_outreach`.
- **Expected**: second contact blocked.
- **Verify**: `test_duplicate_row_same_job_blocked`.

### L-008. message already sent blocked
- **Scenario**: message_sent=1.
- **Trace**: `reach.py:372`.
- **Expected**: blocked.
- **Verify**: assert.

### L-009. message force bypasses
- **Scenario**: `--force`.
- **Trace**: `reach.py:361` force.
- **Expected**: sends.
- **Verify**: `test_force_bypasses_guard`.

### L-010. uncertain pending blocks resend
- **Scenario**: pending attempt on same row.
- **Trace**: `reach.py:379-390` UNCERTAIN_SEND.
- **Expected**: blocked; --set-sent or --force.
- **Verify**: `test_uncertain_pending_blocks_resend`.

### L-011. uncertain pending + force → sends
- **Scenario**: `--force` on pending.
- **Trace**: `reach.py:379` force skip.
- **Expected**: sends.
- **Verify**: `test_uncertain_pending_force_bypasses`.

### L-012. failed attempt does not block
- **Scenario**: failed attempt exists.
- **Trace**: `reach.py:379` status=failed not counted.
- **Expected**: resend allowed.
- **Verify**: `test_failed_attempt_does_not_block`.

### L-013. message no linkedin URL → refuse
- **Scenario**: contact without URL.
- **Trace**: `reach.py:368-370`.
- **Expected**: "No LinkedIn URL".
- **Verify**: assert.

### L-014. message sandbox before browser open
- **Scenario**: sandbox check ordering.
- **Trace**: `reach.py` sandbox before chrome_connect.
- **Expected**: browser never opened under test.
- **Verify**: `test_message_refused_by_sandbox_before_opening_a_browser`.

---

## M. Reach — connect

### M-001. connect success → attempt + flag
- **Scenario**: invitation sent.
- **Trace**: `reach.py:540-544`.
- **Expected**: CONNECT_SENT, reached_out=1, attempt sent.
- **Verify**: assert.

### M-002. connect same-row guard (crash/uncertain)
- **Scenario**: re-run connect after uncertain.
- **Trace**: `reach.py:507-519` ALREADY_CONNECTED_REQUEST.
- **Expected**: blocked; second invitation NOT sent.
- **Verify**: `test_connect_records_attempt_then_blocks_a_second_request`.

### M-003. connect cross-job blocked
- **Scenario**: person reached on other job.
- **Trace**: `reach.py:_prior_outreach`.
- **Expected**: ALREADY_REACHED.
- **Verify**: `test_connect_blocked_cross_job`.

### M-004. connect sandbox refuses before browser
- **Scenario**: test env.
- **Trace**: `reach.py:_sandbox_refused`.
- **Expected**: TEST_SANDBOX, browser not opened.
- **Verify**: `test_connect_refused_by_sandbox_before_opening_a_browser`.

### M-005. connect note default
- **Scenario**: no note → default template.
- **Trace**: `reach.py:527-528`.
- **Expected**: default note with company.
- **Verify**: assert.

### M-006. connect no linkedin URL → refuse
- **Scenario**: contact missing URL.
- **Trace**: `reach.py:498-500`.
- **Expected**: "No LinkedIn URL".
- **Verify**: assert.

---

## N. Reach — update / undo / status

### N-001. update --set-sent email settles pending
- **Scenario**: uncertain email confirmed in inbox.
- **Trace**: `reach.py:cmd_update` set_sent=email.
- **Expected**: email_sent=1; pending attempt settled.
- **Verify**: assert.

### N-002. update --set-sent message settles pending
- **Scenario**: uncertain DM confirmed.
- **Trace**: `reach.py:571-576, 587-598`.
- **Expected**: message_sent=1; pending settled.
- **Verify**: `test_set_sent_settles_the_pending_attempt`.

### N-003. update --set-sent without pending → flag only
- **Scenario**: no pending row (C8 path).
- **Trace**: `reach.py:582` contact_update.
- **Expected**: flag set; no attempt row.
- **Verify**: assert (feeds C8 undo test).

### N-004. update email field
- **Scenario**: backfill email.
- **Trace**: `reach.py:cmd_update --email`.
- **Expected**: contact email updated.
- **Verify**: assert.

### N-005. update note
- **Scenario**: `--note`.
- **Trace**: `reach.py:569-570`.
- **Expected**: notes appended.
- **Verify**: assert.

### N-006. undo refuses without confirm when outreach exists
- **Scenario**: confirmed sends present.
- **Trace**: `reach.py:677-693` REFUSED.
- **Expected**: REFUSED; attempts intact.
- **Verify**: `test_undo_refuses_without_confirm_when_outreach_exists`.

### N-007. undo refuses when ONLY send-flag evidence (C8 FIXED)
- **Scenario**: `update --set-sent` set flag, no attempt row.
- **Trace**: `reach.py` at_risk query includes flags.
- **Expected**: REFUSED without --confirm.
- **Verify**: `test_undo_refuses_when_only_send_flag_set_no_attempt_row`.

### N-008. undo --confirm clears and warns
- **Scenario**: confirmed undo.
- **Trace**: `reach.py:695-704`.
- **Expected**: flags cleared; WARNING about lost protection.
- **Verify**: `test_undo_with_confirm_clears_attempts_and_unblocks`.

### N-009. undo without outreach needs no confirm
- **Scenario**: no evidence.
- **Trace**: `reach.py`.
- **Expected**: undone; no REFUSED.
- **Verify**: `test_undo_without_outreach_needs_no_confirm`.

### N-010. attempts list shows history
- **Scenario**: `reach.py attempts <jid>`.
- **Trace**: `reach.py:cmd_attempts`.
- **Expected**: attempt rows listed.
- **Verify**: assert.

### N-011. status shows channel state
- **Scenario**: `reach.py status`.
- **Trace**: `reach.py:cmd_status`.
- **Expected**: per-contact sent flags.
- **Verify**: assert.

### N-012. person_keys canonicalization
- **Scenario**: URL variants of one profile.
- **Trace**: `reach.py:person_keys` vanity/email.
- **Expected**: variants collapse to one key.
- **Verify**: `test_person_keys` variants.

### N-013. person_keys blank = no key
- **Scenario**: empty url+email.
- **Trace**: `reach.py:person_keys`.
- **Expected**: empty set; identifies nobody.
- **Verify**: `test_person_keys` blank.

### N-014. person_keys URL variants collapse (trailing slash, miniProfileUrn)
- **Scenario**: `?miniProfileUrn=` param.
- **Trace**: `reach.py:67-74`.
- **Expected**: same vanity key.
- **Verify**: assert.

---

## O. State machine / recovery / cross-cutting

### O-001. advance_job legal transitions
- **Scenario**: valid stage advance.
- **Trace**: `lib/db/jobs.py:advance_job`.
- **Expected**: advances.
- **Verify**: assert stage.

### O-002. advance_job illegal transition raises
- **Scenario**: applied → described (illegal).
- **Trace**: `lib/db/jobs.py:advance_job` validation.
- **Expected**: raises/refuses.
- **Verify**: assert error.

### O-003. applied without applied_at suspect
- **Scenario**: applied but no applied_at.
- **Trace**: `report.py applied --suspects`.
- **Expected**: flagged suspect.
- **Verify**: assert suspect listing.

### O-004. recover wrong-applied → tailored
- **Scenario**: recovery of Mongo/Dialpad pattern.
- **Trace**: report/recovery flow.
- **Expected**: stage restored; re-verifiable.
- **Verify**: assert stage.

### O-005. apply_state cleared on reject/undo keeps identity
- **Scenario**: reject a job.
- **Trace**: `page_helpers.py:clear_runtime_state`.
- **Expected**: action flags dropped; identity/answers kept.
- **Verify**: assert.

### O-006. apply_state shared-file lock (JidLock)
- **Scenario**: concurrent different-jid writes.
- **Trace**: `page_helpers.py:save_state` JidLock __apply_state__.
- **Expected**: no clobber.
- **Verify**: `test` concurrency.

### O-007. per-job lock (F5)
- **Scenario**: two processes same jid.
- **Trace**: `apply/common/jid_lock.py` PID+TTL reap.
- **Expected**: second blocked/waits.
- **Verify**: assert.

### O-008. stale lock TTL reaped
- **Scenario**: dead process lock.
- **Trace**: `jid_lock.py` TTL.
- **Expected**: lock reaped.
- **Verify**: assert.

### O-009. URN read-back trap
- **Scenario**: LinkedIn URN passed as answer to non-URN field.
- **Trace**: read-back guard.
- **Expected**: rejected; not filled.
- **Verify**: assert.

### O-010. `reinterpreted` → `unverified` honesty
- **Scenario**: normalization not safe.
- **Trace**: `_is_safe_normalization` path.
- **Expected**: marked unverified, not silent OK.
- **Verify**: assert status.

### O-011. risk-field split (prefilled kind + value surfaced)
- **Scenario**: prefilled field with kind/value.
- **Trace**: dossier surfacing.
- **Expected**: both surfaced.
- **Verify**: assert.

### O-012. dossier is truth; apply_state is runtime cache
- **Scenario**: apply_state diverges from dossier.
- **Trace**: `page_helpers.py:save_state` `_role` marker.
- **Expected**: dossier authoritative.
- **Verify**: assert role.

### O-013. submit one-shot survives process race
- **Scenario**: two workers race submit.
- **Trace**: `mark_applied` + submit_clicked + lock.
- **Expected**: one applied.
- **Verify**: assert single applied row.

### O-014. pacing between sends
- **Scenario**: outreach batch pacing.
- **Trace**: reach pacing config.
- **Expected**: delays between transmits.
- **Verify**: assert timing.

### O-015. `report.py fleet` accuracy view
- **Scenario**: fleet accuracy report.
- **Trace**: `report.py fleet`.
- **Expected**: per-job outcome + accuracy.
- **Verify**: assert output.

### O-016. `report.py applied --suspects` clean fleet
- **Scenario**: post-recovery scan.
- **Trace**: `report.py applied --suspects`.
- **Expected**: 0 suspects.
- **Verify**: assert empty.

### O-017. shadow never mutates job state
- **Scenario**: shadow run on live jobs.
- **Trace**: `apply.py shadow` subprocess isolation.
- **Expected**: no stage changes.
- **Verify**: assert state snapshot equal.

---

## P. Adversarial / curveballs (regression guards)

### P-001. `--next` refuses "Continue to Review" (C1)
- **Trace**: `inspect.py:cmd_next` BUTTON_GATE.
- **Verify**: `NextButtonSubmitGate.test_submit_like_button_refused`.

### P-002. `--next` after submit_clicked refuses (C2)
- **Verify**: `NextButtonSubmitGate.test_submit_clicked_refuses_next`.

### P-003. Antigua pick prevention (C0)
- **Trace**: `strategies/select.py:_pick_option`.
- **Verify**: `test_verify_redteam` country-name containment.

### P-004. dialing-code map coverage
- **Verify**: `test_dialing_codes` common countries.

### P-005. native select index bypass (B)
- **Verify**: `NativeSelectStrategy.test_value_differs_from_text_selects_by_index`.

### P-006. captcha false-OK login closed (C-C2)
- **Verify**: `LoginWallCaptcha.test_login_captcha_returns_captcha_required_and_never_promotes`.

### P-007. creds never typed on unapproved domain (2-A)
- **Verify**: `test_unapproved_domain_refuses_creds`.

### P-008. undo can't silently disarm guard (C8)
- **Verify**: `test_undo_refuses_when_only_send_flag_set_no_attempt_row`.

### P-009. blank identity surfaced not silent (C7)
- **Verify**: `test_blank_identity_warns_but_does_not_block`.

### P-010. wrong-applied non-target text not success
- **Verify**: `AlreadyAppliedTargetGuard` non-target case.

---

## Q. `ji` orchestrator surface

### Q-001. `ji status` aggregates fleet
- **Trace**: `ji.py:cmd_status`.
- **Expected**: stage/status per job.
- **Verify**: assert output.

### Q-002. `ji ready` lists fillable
- **Trace**: `ji.py:cmd_ready`.
- **Expected**: tailored+active jobs.
- **Verify**: assert filter.

### Q-003. `ji verify --all` pre-submit verification
- **Trace**: `ji.py:cmd_verify`.
- **Expected**: verifies all ready.
- **Verify**: assert no applied mutation.

### Q-004. `ji job <jid>` dossier view
- **Trace**: `ji.py:cmd_job`.
- **Expected**: handoff.json contents.
- **Verify**: assert.

### Q-005. `ji diff` profile vs dossier
- **Trace**: `ji.py:cmd_diff`.
- **Expected**: differences listed.
- **Verify**: assert.

### Q-006. `ji audit` compliance run
- **Trace**: `ji.py:cmd_audit`.
- **Expected**: audit report.
- **Verify**: assert.

### Q-007. `ji fetch` runs full ingestion
- **Trace**: `ji.py:cmd_fetch`.
- **Expected**: stage_emails+extract+enrich+tailor.
- **Verify**: assert jobs created.

### Q-008. `ji apply` delegates to apply.py
- **Trace**: `ji.py:cmd_apply`.
- **Expected**: passthrough.
- **Verify**: assert.

### Q-009. `ji submit` gated passthrough
- **Trace**: `ji.py:cmd_submit`.
- **Expected**: submit gates intact.
- **Verify**: assert one-shot.

### Q-010. `ji shadow` passthrough
- **Trace**: `ji.py:cmd_shadow`.
- **Expected**: shadow behavior.
- **Verify**: assert.

### Q-011. `ji answer` sets profile answer
- **Trace**: `ji.py:cmd_answer`.
- **Expected**: profile updated.
- **Verify**: assert.

### Q-012. `ji decisions` risk-unverified list
- **Trace**: `ji.py:cmd_decisions` `_risk_unverified`.
- **Expected**: flags unverified risk.
- **Verify**: assert.

---

## R. Performance / robustness

### R-001. fill loop deadline honored (job_timeout_sec)
- **Trace**: `fill.py:368-377` `_abort_timed_out`.
- **Expected**: STATUS_TIMED_OUT; resumable.
- **Verify**: assert.

### R-002. captcha wait bounded by deadline
- **Trace**: `fill.py:390` wait_s = deadline-based.
- **Expected**: aborts within budget.
- **Verify**: assert.

### R-003. `_wait_for_fields` patience
- **Trace**: `helpers.py:_wait_for_fields`.
- **Expected**: waits for lazy fields.
- **Verify**: assert.

### R-004. `_get_validation_errors` robust parse
- **Trace**: `helpers.py:_get_validation_errors`.
- **Expected**: returns list; never raises.
- **Verify**: assert.

### R-005. `_empty_required` identifies blanks
- **Trace**: `helpers.py:_empty_required`.
- **Expected**: blank required listed.
- **Verify**: assert.

### R-006. chrome_session isolation (--isolate)
- **Trace**: `helpers.py:_isolate_session`.
- **Expected**: isolated context.
- **Verify**: assert.

### R-007. detached-frame exceptions swallowed
- **Scenario**: element detaches mid-fill.
- **Trace**: fill/evaluate try/except.
- **Expected**: falls to next method, no crash.
- **Verify**: assert method chain continues.

### R-008. registry resolution failure safe
- **Trace**: `page_helpers.py:253-255` except.
- **Expected**: proceeds without custom widgets.
- **Verify**: assert no crash.

### R-009. ask_api unavailable → policy off path
- **Trace**: `lib/ask_api.py`, `lib/automation/llm.py` allow gate.
- **Expected**: short-circuits; no API touch.
- **Verify**: `test_unavailable`.

### R-010. large body pages read bounded
- **Trace**: `page_helpers.py:page_text`.
- **Expected**: no unbounded memory.
- **Verify**: assert.

---

## S. Honest-failure / no-false-success invariants

### S-001. never mark applied without success evidence
- **Trace**: `submit.py` + `verify.py`.
- **Expected**: applied only on signals.
- **Verify**: assert.

### S-002. never silently resend DM/email
- **Trace**: reach one-shot flags + attempts.
- **Expected**: every transmit recorded.
- **Verify**: assert.

### S-003. never type creds into unapproved domain
- **Trace**: `fill.py:1001`.
- **Verify**: `test_unapproved_domain_refuses_creds`.

### S-004. never guess a country/dialing code
- **Trace**: `resolve.py` + `_pick_option`.
- **Verify**: `test_phone_country_code_no_country_is_no_match`.

### S-005. never record account created under captcha
- **Trace**: `fill.py:_check_account_created` "captcha".
- **Verify**: `test_check_account_created_surfaces_captcha`.

### S-006. never promote password on captcha-blocked login
- **Verify**: `LoginWallCaptcha.test_login_captcha_returns_captcha_required_and_never_promotes`.

### S-007. verify second observer for ambiguous submit
- **Trace**: `verify.py:_vision_confirms`.
- **Verify**: assert called only when ambiguous.

### S-008. shadow never submits (regression)
- **Trace**: `apply.py shadow`.
- **Verify**: assert no applied transition.

### S-009. `--force` is explicit, never implicit
- **Trace**: reach/submit force flags.
- **Verify**: assert only with flag.

### S-010. unrecoverable class documented (UNRECOVERABLE.md)
- **Verify**: doc maintained.

---

## Notes for the test harness

- **Fast vs live**: cases marked `VERIFY=test_*` are covered by the unit suite
  (`pytest tests -q`); others need a live/browser harness or a fake-page
  (`apply/common/mock_page.py`) driver.
- **Data setup**: temp DB via `_TempDBMixin` (tests/test_reach.py) and
  `schema.DB_PATH` override; profile in `~/.ji/profile.json`.
- **Trace labels**: `file:line` is approximate to the pass the fix landed in;
  re-grep the symbol if drift.
- **Status**: mark each case PASS/FAIL/FOUND as you run it; a FOUND case that
  is a safety invariant (S-*, G-*, P-*) should be fixed and pinned before
  shipping.
