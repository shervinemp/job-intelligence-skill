# Test Corpus — Varied Solutions per Case

Companion to TEST_CORPUS.md. For every case, 2–4 **varied** solution /
verification approaches. `S1/S2/S3` are alternatives, not a sequence — pick the
cheapest that still pins the behavior. Legend: **U**=unit (mock), **F**=fake
page (`apply/common/mock_page.py`), **L**=live browser, **D**=data-driven,
**DB**=schema temp-DB test.

---

## A. Ingestion & classification

### A-001 Gmail fetch with attachment
- **S1 (U)**: mock gmail-cli stdout (JSON with attachment id) → assert `extract.py` produces a job row.
- **S2 (U)**: stub `stage_emails` search + `extract`; assert url/message-id dedup key.
- **S3 (L)**: real fetch against a test Gmail label; assert 1 new described job, idempotent on re-run.

### A-002 Duplicate email idempotent
- **S1 (DB)**: insert job with url; re-run extract; assert `COUNT(*)` == 1.
- **S2 (U)**: patch dedup fn to return existing id; assert upsert (not insert).
- **S3 (D)**: parametrize over url-normalization variants (trailing slash, case).

### A-003 LinkedIn via linkedin.py
- **S1 (U)**: stub linkedin list → assert platform=linkedin, external_url set.
- **S2 (L)**: `linkedin.py --max 1` against test account; assert described job.

### A-004 enrich resolves domain
- **S1 (D)**: companies DB with known slug; assert domain+slug filled.
- **S2 (U)**: mock company resolver; assert mapping applied.
- **S3 (DB)**: enrich twice → no duplicate company rows.

### A-005 enrich unknown company honest
- **S1 (U)**: resolver returns None → assert domain stays empty.
- **S2 (D)**: unknown-name corpus → assert no string that looks like a domain.

### A-006 tailor generates summary
- **S1 (U)**: mock `build_resume`; assert stage tailored + summary present.
- **S2 (F)**: full tailor with profile → assert summary non-empty, contains keywords.
- **S3 (DB)**: tailor idempotent (re-run doesn't duplicate).

### A-007 tailor skips on incomplete profile
- **S1 (U)**: empty profile → assert stage stays described + quality reason recorded.
- **S2 (DB)**: set `profile.location` missing; run; assert blocked.
- **S3 (D)**: parametrize over each required field missing.

### A-008 classifier data-driven
- **S1 (D)**: default_answers.json variations → assert resolved answer changes.
- **S2 (U)**: swap classifier source; assert no hardcode path used.

### A-009 pronouns map
- **S1 (D)**: cover known set; unknown → safe default, no crash.
- **S2 (U)**: feed `"xir/xem"`; assert default not exception.

### A-010 dialing-code map
- **S1 (D)**: `country_iso` table for common countries + `""` for Atlantis.
- **S2 (U)**: round-trip iso→code→iso.

### A-011 alias rules
- **S1 (DB)**: add_alias_rule country→location; resolve; assert maps.
- **S2 (U)**: no rule → needs_data.
- **S3 (D)**: aliases with wildcards/plurals.

### A-012 field-method learning scoped
- **S1 (DB)**: record method on domain A; `prefer_method` A returns it, B returns "".
- **S2 (D)**: parametrize over 2+ domains; assert isolation.

### A-013 verify-strategy learning
- **S1 (DB)**: record twice; assert preferred on same domain+field.
- **S2 (U)**: no record → default chain.

### A-014 adjudication retracts learning
- **S1 (DB)**: learn then `wrong`; assert `_lookup_learned` None.
- **S2 (U)**: `wrong` also retracts alias rule + profile-suspect + field-method.
- **S3 (DB)**: re-run after retract → re-derives fresh.

### A-015 adjudication ok keeps
- **S1 (DB)**: `ok` → method still preferred.
- **S2 (U)**: `ok` does not retract anything.

### A-016 SPC tripwire
- **S1 (U)**: fake N wrong fills → assert health drops / tripwire fires.
- **S2 (DB)**: seed ledger with wrongs; run `report.py fleet`; assert flag.

### A-017 dedup by URL
- **S1 (DB)**: same url two sources → one row.
- **S2 (U)**: second source updates existing (not duplicate).

### A-018 active filter
- **S1 (DB)**: job state=closed → excluded from discover_all.
- **S2 (U)**: `cmd_discover_all` query filter assert.

### A-019 extract fallback
- **S1 (U)**: ask_api unavailable → graceful needs_data.
- **S2 (F)**: stub structured parse fail → fallback path taken.
- **S3 (U)**: fallback then ask_api available → structured result used.

### A-020 ask_api minimization
- **S1 (U)**: assert ask_api not called for text classification in auto mode.
- **S2 (U)**: assert vision-only routing.

---

## B. Apply — detect & navigate

### B-001 detect external
- **S1 (U)**: job w/ external_url → detect returns external.
- **S2 (F)**: fake page; assert external navigation called.

### B-002 detect easy_apply
- **S1 (U)**: no external_url → easy_apply.
- **S2 (F)**: assert modal opened.

### B-003 redirect host change
- **S1 (F)**: page.url differs from requested; assert external_url updated.
- **S2 (L)**: real 301 ATS; assert state final host.

### B-004 broken landing → fallbacks
- **S1 (F)**: fake error page; assert fallback list tried.
- **S2 (U)**: `_url_fallbacks` ordering test.
- **S3 (L)**: dead URL → alternates.

### B-005 cross-origin form
- **S1 (F)**: page links to standalone form; assert navigation.
- **S2 (U)**: `_resolve_standalone_form_url` unit.

### B-006 apply button click
- **S1 (F)**: button present; assert click.
- **S2 (U)**: `_click_apply_button` scoring.

### B-007 no apply path
- **S1 (U)**: `_no_apply_path_detail` returns reason; assert status.
- **S2 (F)**: page without apply; assert no_apply_path.

### B-008 session timeout popup
- **S1 (F)**: title contains session/time out; assert Keep Working clicked.
- **S2 (U)**: title mismatch → returns False.

### B-009 cookie banner
- **S1 (F)**: Accept Cookies visible; assert clicked once.
- **S2 (U)**: no banner → loop skips silently.

### B-010 dialogs wired
- **S1 (F)**: native alert(); assert auto-dismiss.
- **S2 (U)**: `_wire_dialogs` handler registered.

### B-011 tag_page
- **S1 (F)**: two tabs, one tagged; assert find by tag.
- **S2 (U)**: `_PAGE_JID_MAP` fallback.

### B-012 find_page scoring
- **S1 (F)**: exact/prefix scoring; assert best page.
- **S2 (U)**: score function unit.
- **S3 (F)**: li job id in url fallback.

### B-013 redirect one-shot safe
- **S1 (U)**: assert submit gate keyed to final host.
- **S2 (F)**: navigate w/ redirect; submit refused on unapproved host.

### B-014 apply in dialog
- **S1 (F)**: button in `[role=dialog]`; assert `_inDialog`.
- **S2 (U)**: `scan_actions` dialog scope.

### B-015 guest apply
- **S1 (F)**: registry guest pattern; assert GUEST_APPLY.
- **S2 (U)**: pattern not present → skip silently.

### B-016 custom widget
- **S1 (F)**: Ashby autocomplete; assert field_role combobox.
- **S2 (U)**: `field_types.py` role decision.

### B-017 registry override
- **S1 (U)**: registry.widgets parent used in read.
- **S2 (F)**: fake registry → custom selector applied.

### B-018 probe roles
- **S1 (F)**: form mix; assert roles standard/combobox/select.
- **S2 (U)**: `capabilities.py` role heuristics.

### B-019 iframe merge
- **S1 (F)**: iframe fields > page fields; assert merged.
- **S2 (U)**: dedup by label on merge.

### B-020 progress bar
- **S1 (F)**: `[role=progressbar]`; assert has_progress_bar.
- **S2 (U)**: visible-only check.

---

## C. Apply — read & probe

### C-001 read_fields document
- **S1 (F)**: simple form; assert fieldCount>0, all visible inputs.
- **S2 (U)**: `read_fields` selector unit.

### C-002 dialog scope fallback
- **S1 (F)**: fields only in modal; assert `_scoped_to=dialog`.
- **S2 (F)**: 0 document fields → dialog retry.

### C-003 normalization
- **S1 (U)**: "E-mail"→"email"; accents.
- **S2 (D)**: parametrized label corpus.

### C-004 junk filter
- **S1 (F)**: hidden/search/honeypot; assert excluded.
- **S2 (U)**: `_is_junk_field` cases.

### C-005 honeypot
- **S1 (F)**: hidden "website" input; assert honeypot_signals>0.
- **S2 (U)**: aria-hidden + tabindex=-1 logic.

### C-006 required detection
- **S1 (F)**: required attr + asterisk; assert required flags.
- **S2 (U)**: shapes required heuristics.

### C-007 select read-back
- **S1 (F)**: SELECT selectedIndex; assert option text.
- **S2 (U)**: `value_reader.py:51` branch.

### C-008 custom read-back
- **S1 (F)**: react-select; assert single-value text.
- **S2 (U)**: aria-selected lookup.

### C-009 combobox pre-filled skip
- **S1 (F)**: `.select__single-value` present; assert skip.
- **S2 (U)**: continue-on-present logic.

### C-010 aria-selected skip
- **S1 (F)**: option aria-selected=true; assert not clobbered.
- **S2 (U)**: `helpers.py:383`.

### C-011 options probe
- **S1 (F)**: native select; assert first 15 option texts.
- **S2 (U)**: `.slice(0,15)` bound.

### C-012 date field type
- **S1 (U)**: type=date → native.
- **S2 (F)**: datepicker strategy chosen.

### C-013 contenteditable
- **S1 (U)**: `contenteditable.py` branch.
- **S2 (F)**: rich-text fill.

### C-014 multi-iframe dedup
- **S1 (F)**: same label page+iframe; assert one entry.
- **S2 (U)**: existing-set logic.

### C-015 shadow text
- **S1 (F)**: shadowRoot text; assert in page_text.
- **S2 (U)**: `:defined` walk.

---

## D. Apply — fill strategies

### D-001 standard text fill
- **S1 (F)**: plain input; assert fill method + read-back match.
- **S2 (U)**: METHOD_CHAIN first-hit.

### D-002 native_setter fallback
- **S1 (F)**: controlled input; assert method=native_setter, events dispatched.
- **S2 (U)**: `text.py:native_setter` sets el.value.

### D-003 autocomplete typeahead
- **S1 (F)**: assert autocomplete used after `fill("")`.
- **S2 (U)**: clear-then-type sequence.

### D-004 dispatch_events
- **S1 (F)**: assert input+change fired.
- **S2 (U)**: last-resort ordering.

### D-005 select_option
- **S1 (F)**: value==text options; assert selected.
- **S2 (U)**: Playwright label match.

### D-006 index-based native select
- **S1 (F)**: value≠text + lazy DOM; assert selectedIndex set via el.options.
- **S2 (U)**: `_set_native_by_index` unit with fake el.
- **S3 (L)**: CyberCoders live phone-country.

### D-007 js_click select
- **S1 (F)**: assert el.selectedIndex set from matched option.
- **S2 (U)**: paren-dialing fallback.

### D-008 country beats bare code
- **S1 (U)**: `_pick_option` with country_words → Canada.
- **S2 (D)**: option corpus with ambiguous +1.

### D-009 known-country-not-loaded → None
- **S1 (U)**: Canada absent → None (never Antigua).
- **S2 (F)**: try_select_tag returns False → orchestrator.

### D-010 bare code w/o country
- **S1 (U)**: no country_words → paren match.
- **S2 (F)**: accepts risk.

### D-011 combobox chain
- **S1 (F)**: assert combobox strategy path.
- **S2 (U)**: `combobox.py` click→type→pick.

### D-012 datepicker
- **S1 (F)**: assert ISO value.
- **S2 (U)**: strategy chosen by type.

### D-013 contenteditable fill
- **S1 (F)**: assert content + events.
- **S2 (U)**: fallback if events unsupported.

### D-014 phone country resolves
- **S1 (U)**: resolve("Phone country code",+1,Canada) → CANADA.
- **S2 (F)**: field-level fill.

### D-015 phone no country → needs_data
- **S1 (U)**: no location → needs_data, never code.
- **S2 (F)**: orchestrator skip.

### D-016 tel autocomplete
- **S1 (U)**: autocomplete=tel-country-code → country.
- **S2 (D)**: variants.

### D-017 postal not country
- **S1 (U)**: "M5V 2T6" → not country.
- **S2 (F)**: end-to-end resolve.

### D-018 postal suffix stripped
- **S1 (U)**: "Canada G1R 5J4" → Canada.
- **S2 (D)**: location corpus.

### D-019 conditional reveal
- **S1 (F)**: click radio → new fields appear → filled.
- **S2 (U)**: reveal sweep triggers.

### D-020 required unanswered
- **S1 (F)**: required field unanswered → REQUIRED in status.
- **S2 (U)**: req_no_answer filtering.

### D-021 pre-filled fast path
- **S1 (F)**: submit visible → skip fill → next check.
- **S2 (U)**: emit_next check.

### D-022 zero fields → inspect
- **S1 (F)**: no fillable → emit_next inspect.
- **S2 (U)**: not applied.

### D-023 radio group
- **S1 (F)**: select expected option.
- **S2 (U)**: value-vs-label compare.

### D-024 cross-field coherence
- **S1 (F)**: country vs location mismatch → flag.
- **S2 (U)**: `check.py:381` compare.

### D-025 sponsorship
- **S1 (U)**: profile-derived yes/no.
- **S2 (F)**: answer matches profile.

---

## E. Apply — check & validate

### E-001 check filled values
- **S1 (F)**: fill then check; assert diff list.
- **S2 (U)**: `cmd_check` compare fn.

### E-002 wrong country flagged
- **S1 (F)**: filled≠profile country; assert issue.
- **S2 (U)**: country compare.

### E-003 react widget compare
- **S1 (U)**: `_is_react_widget` true → widget compare.
- **S2 (F)**: no false flag on react-select.

### E-004 normalize compare
- **S1 (U)**: case/whitespace equal.
- **S2 (D)**: normalization corpus.

### E-005 emit next value
- **S1 (F)**: check pass → next submit.
- **S2 (U)**: emit_next_value routing.

### E-006 check applied short-circuit
- **S1 (DB)**: stage=applied → no browser.
- **S2 (U)**: early return assert.

### E-007 verify preconditions
- **S1 (F)**: pending submit → verify signals.
- **S2 (L)**: live post-submit page.

### E-008 confirmation URL
- **S1 (U)**: `_is_confirmation_url` true.
- **S2 (D)**: url corpus.

### E-009 registrable domain
- **S1 (U)**: fake ATS domain rejected.
- **S2 (D)**: public-suffix list cases.

### E-010 vision confirm
- **S1 (U)**: ambiguous → `_vision_confirms` called.
- **S2 (F)**: not sole authority (needs text too).

---

## F. Apply — next / inspect / investigate

### F-001 --next mid-form
- **S1 (U)**: mock page/button; assert click + emit fill.
- **S2 (F)**: fake chrome_session.

### F-002 --next refuses submit-like
- **S1 (U)**: "Continue to Review" → BUTTON_GATE, not clicked, routes submit.
- **S2 (F)**: assert no click side effect.

### F-003 --next submit_clicked refuses
- **S1 (U)**: state submit_clicked → GUARD, chrome_session not called.
- **S2 (F)**: assert no page open.

### F-004 --next no button
- **S1 (F)**: none → error, no click.
- **S2 (U)**: return 1.

### F-005 inspect dumps
- **S1 (L)**: run on stuck page; assert html dump + screenshot.
- **S2 (F)**: capture calls.

### F-006 investigate captcha
- **S1 (U)**: handle_captcha true → captcha_required.
- **S2 (F)**: waits human then resolves.

### F-007 next keyword scoring
- **S1 (U)**: `_find_next_button` score order.
- **S2 (F)**: multiple candidates → highest wins.

### F-008 click scrolls into view
- **S1 (U)**: `_click_action` scroll called.
- **S2 (F)**: below-fold button clicked.

### F-009 disabled not clicked
- **S1 (F)**: disabled → not clicked.
- **S2 (U)**: disabled flag honored.

### F-010 next navigation
- **S1 (L)**: multi-step ATS; assert new step fields.
- **S2 (F)**: state external_url updated.

---

## G. Apply — submit (one-shot safety)

### G-001 duplicate submit blocked
- **S1 (U)**: submit_clicked set → refuse, no second click.
- **S2 (DB)**: flag persisted.

### G-002 submit success
- **S1 (DB)**: success → applied+applied_at, guard cleared.
- **S2 (F)**: success signal then mark_applied.

### G-003 mark_applied atomic
- **S1 (DB)**: two threads; assert one transition.
- **S2 (U)**: WHERE stage != applied.

### G-004 on-target applied text success
- **S1 (F)**: text on target url → success.
- **S2 (U)**: target_url threading.

### G-005 non-target not success
- **S1 (F)**: text on other tab → NOT applied.
- **S2 (DB)**: Mongo/Dialpad repro.

### G-006 validation errors
- **S1 (F)**: field errors → validation_error, stays filled.
- **S2 (U)**: `_determine_outcome`.

### G-007 captcha at submit
- **S1 (U)**: handle_captcha → captcha_required.
- **S2 (F)**: human solve resumes.

### G-008 success modal
- **S1 (F)**: dialog "submitted" → success.
- **S2 (U)**: successModalText flag.

### G-009 uncertain → investigate
- **S1 (F)**: no signal → uncertain, no re-click.
- **S2 (L)**: live ambiguous submit.

### G-010 already applied early return
- **S1 (DB)**: stage=applied → no browser.
- **S2 (U)**: stage check first.

### G-011 duplicate URL
- **S1 (DB)**: same url applied elsewhere → warning.
- **S2 (U)**: dup query.

### G-012 form still present
- **S1 (F)**: form remains → not success.
- **S2 (U)**: `_form_still_present`.

### G-013 force only
- **S1 (U)**: force bypass allowed.
- **S2 (DB)**: flag recorded.

### G-014 undo clears
- **S1 (DB)**: undo → submit_clicked cleared.
- **S2 (F)**: re-run possible.

### G-015 applied survives state clear
- **S1 (DB)**: state cleared → applied kept.
- **S2 (U)**: DB truth.

---

## H. Apply — auto / shadow / batch / retry

### H-001 shadow no submit
- **S1 (DB)**: run shadow; assert no applied transitions.
- **S2 (F)**: subprocess isolation.

### H-002 shadow resumable
- **S1 (DB)**: kill mid-run; resume; assert skip done.
- **S2 (U)**: log append.

### H-003 preflight gates
- **S1 (U)**: incomplete profile → gate message.
- **S2 (DB)**: fleet blocked.

### H-004 LLM retry fill
- **S1 (U)**: fill fail → `_retry_fill_with_llm` bounded.
- **S2 (F)**: retry count assert.

### H-005 LLM retry submit
- **S1 (U)**: validation → retry, one-shot respected.
- **S2 (DB)**: no double applied.

### H-006 batch limit
- **S1 (U)**: `--limit 3` → ≤3.
- **S2 (DB)**: subset processing.

### H-007 quick mode
- **S1 (U)**: quick skips verify.
- **S2 (F)**: verify not called.

### H-008 retry FAILED
- **S1 (DB)**: FAILED → retry re-processes.
- **S2 (U)**: stage path.

### H-009 retry-skipped
- **S1 (DB)**: SKIPPED → re-examine.
- **S2 (U)**: skip queue.

### H-010 shadow --recheck
- **S1 (U)**: unconfirmed skips re-examined.
- **S2 (DB)**: queue scanned.

### H-011 error labels
- **S1 (U)**: `_extract_error_labels` parse.
- **S2 (D)**: message corpus.

### H-012 fleet scan
- **S1 (DB)**: clean fleet → 0 suspects.
- **S2 (L)**: live scan.

### H-013 --job-id filter
- **S1 (U)**: only jid processed.
- **S2 (DB)**: subset.

---

## I. Auth — login / signup / captcha / 2FA

### I-001 no wall
- **S1 (U)**: `_LOGIN_JS` None → "".
- **S2 (F)**: no password input.

### I-002 auto-login approved
- **S1 (U)**: approved + creds → filled, OK.
- **S2 (F)**: `_fill_signin_form` called.

### I-003 unapproved refuses
- **S1 (U)**: `_domain_approved` False → CRED_GUARD, fill not called.
- **S2 (DB)**: domain_gate rows.

### I-004 alt password promoted
- **S1 (U)**: `["no","yes"]` → winner promoted, others remain.
- **S2 (DB)**: save_creds args.

### I-005 2FA surfaces
- **S1 (U)**: "2fa" → 2fa_required, no more tries.
- **S2 (F)**: user left at prompt.

### I-006 all fail
- **S1 (U)**: "no" ×N → login_failed.
- **S2 (F)**: error printed.

### I-007 login captcha
- **S1 (U)**: `_login_check` "captcha" → captcha_required, no creds saved.
- **S2 (F)**: widget visible.

### I-008 uncertain then captcha
- **S1 (U)**: `["uncertain","captcha"]` → captcha_required.
- **S2 (F)**: re-check path.

### I-009 `_login_check` captcha signal
- **S1 (U)**: `check_captcha` True + uncertain → "captcha".
- **S2 (F)**: no widget → "uncertain".

### I-010 runtime detector recaptcha
- **S1 (U)**: evaluate returns True → detected.
- **S2 (F)**: `.g-recaptcha` visible.

### I-011 account created
- **S1 (U)**: "yes" → creds saved + shared pool.
- **S2 (F)**: create form filled.

### I-012 account captcha no save
- **S1 (U)**: `_check_account_created` "captcha" → captcha_required, save NOT called.
- **S2 (DB)**: no password on disk.

### I-013 account exists → pool
- **S1 (U)**: "exists" → sign-in loop with pool.
- **S2 (F)**: matched pool password.

### I-014 create rejected
- **S1 (U)**: "no" → login_required + validation errors.
- **S2 (F)**: weak password.

### I-015 generated password
- **S1 (U)**: `gen_password_for_platform` compliant.
- **S2 (D)**: rule corpus.

### I-016 cookie banner
- **S1 (F)**: accept cookies clicked.
- **S2 (U)**: selector loop.

### I-017 create→signin switch
- **S1 (F)**: signInLink clicked; sign-in filled.
- **S2 (U)**: Workday selector.

### I-018 uncertain wait
- **S1 (U)**: uncertain → sleep+recheck once.
- **S2 (F)**: recheck yes.

### I-019 error text → no
- **S1 (U)**: "Incorrect password" → "no".
- **S2 (D)**: error corpus.

### I-020 2FA input
- **S1 (U)**: autocomplete=one-time-code → "2fa".
- **S2 (D)**: maxlength 4–8 numeric.

---

## J. Reach — discover & list

### J-001 discover
- **S1 (DB)**: contacts stored, contact_discovered=1.
- **S2 (U)**: discover_contacts mock.

### J-002 --team
- **S1 (DB)**: team contacts added.
- **S2 (U)**: team source.

### J-003 --all
- **S1 (DB)**: iterates described/tailored active.
- **S2 (U)**: NO_JOBS_TO_DISCOVER.

### J-004 --limit
- **S1 (DB)**: ≤2.
- **S2 (U)**: slice.

### J-005 list order
- **S1 (DB)**: index matches --contact N.
- **S2 (U)**: enumerate.

### J-006 discover error
- **S1 (U)**: ERROR printed, no crash.
- **S2 (F)**: browser fail.

### J-007 re-discover
- **S1 (DB)**: flag reset then discover.
- **S2 (U)**: retry path.

### J-008 email candidates
- **S1 (DB)**: candidates printed with confidence.
- **S2 (U)**: suggested_emails.

### J-009 blank identity in list
- **S1 (DB)**: blank row present.
- **S2 (U)**: no crash.

---

## K. Reach — email

### K-001 email success
- **S1 (DB)**: EMAIL_SENT; email_sent=1; attempt sent; event.
- **S2 (U)**: subprocess fake stdout sent.

### K-002 sandbox
- **S1 (U)**: JI_TESTS → TEST_SANDBOX; no transmit.
- **S2 (U)**: gmail-cli guard exit 3.

### K-003 already sent
- **S1 (DB)**: email_sent=1 → blocked.
- **S2 (U)**: flag check.

### K-004 cross-job
- **S1 (DB)**: prior outreach → ALREADY_REACHED.
- **S2 (U)**: `_prior_outreach`.

### K-005 failed recorded
- **S1 (U)**: returncode 1 → attempt failed; email_sent=0.
- **S2 (DB)**: row present.

### K-006 timeout
- **S1 (U)**: TimeoutExpired → attempt failed.
- **S2 (DB)**: recorded.

### K-007 gmail missing
- **S1 (U)**: FileNotFoundError → attempt failed.
- **S2 (U)**: message.

### K-008 dry-run
- **S1 (DB)**: no transmission, no flags.
- **S2 (U)**: subprocess not called.

### K-009 body file
- **S1 (U)**: body from file.
- **S2 (F)**: content used.

### K-010 bad recipient
- **S1 (U)**: refused before send.
- **S2 (DB)**: no attempt row.

### K-011 blank identity warning
- **S1 (U)**: `_block_if_prior` blank → warning, not block.
- **S2 (DB)**: no ALREADY_REACHED.

### K-012 uncertain email
- **S1 (U)**: uncertain → verify hint + --set-sent settles.
- **S2 (DB)**: pending settled.

---

## L. Reach — message (DM)

### L-001 message success
- **S1 (DB)**: MESSAGE_SENT; attempt sent; event.
- **S2 (U)**: send_message mock.

### L-002 sandbox
- **S1 (U)**: TEST_SANDBOX; browser not opened.
- **S2 (U)**: `_sandbox_refused`.

### L-003 deep sandbox
- **S1 (U)**: direct `send_message` → sandbox_refused.
- **S2 (U)**: `send_connect_request`.

### L-004 cross-job
- **S1 (DB)**: prior → ALREADY_REACHED.
- **S2 (U)**: keys intersect.

### L-005 uncertain attempt cross-job
- **S1 (DB)**: pending attempt other job → blocked.
- **S2 (U)**: status IN pending.

### L-006 connect→DM funnel
- **S1 (DB)**: same row funnel allowed.
- **S2 (U)**: current row excluded.

### L-007 duplicate row
- **S1 (DB)**: same person 2 rows → 2nd blocked.
- **S2 (U)**: keys match.

### L-008 already messaged
- **S1 (DB)**: message_sent=1 → blocked.
- **S2 (U)**: flag.

### L-009 force
- **S1 (U)**: --force sends.
- **S2 (DB)**: second row allowed.

### L-010 pending blocks resend
- **S1 (DB)**: pending same row → UNCERTAIN_SEND.
- **S2 (U)**: status pending.

### L-011 pending + force
- **S1 (U)**: force skips pending → sends.
- **S2 (DB)**: attempt sent.

### L-012 failed doesn't block
- **S1 (DB)**: failed attempt → resend OK.
- **S2 (U)**: status=failed excluded.

### L-013 no URL refuse
- **S1 (DB)**: contact no url → "No LinkedIn URL".
- **S2 (U)**: early return.

### L-014 sandbox before browser
- **S1 (U)**: chrome_connect not called.
- **S2 (F)**: ordering assert.

---

## M. Reach — connect

### M-001 connect success
- **S1 (DB)**: CONNECT_SENT; attempt sent; reached_out=1.
- **S2 (U)**: send_connect_request mock.

### M-002 same-row guard
- **S1 (DB)**: re-run → ALREADY_CONNECTED_REQUEST, 1 invitation.
- **S2 (U)**: side_effect count.

### M-003 cross-job
- **S1 (DB)**: prior → ALREADY_REACHED.
- **S2 (U)**: `_block_if_prior`.

### M-004 sandbox
- **S1 (U)**: TEST_SANDBOX before browser.
- **S2 (F)**: chrome not opened.

### M-005 default note
- **S1 (U)**: no note → template w/ company.
- **S2 (DB)**: body recorded.

### M-006 no URL
- **S1 (DB)**: refuse.
- **S2 (U)**: early return.

---

## N. Reach — update / undo / status

### N-001 set-sent email
- **S1 (DB)**: email_sent=1; pending settled.
- **S2 (U)**: attempt update.

### N-002 set-sent message
- **S1 (DB)**: message_sent=1; pending settled.
- **S2 (U)**: `test_set_sent_settles_the_pending_attempt`.

### N-003 set-sent flag only
- **S1 (DB)**: no pending → flag set, no row.
- **S2 (U)**: update path.

### N-004 update email
- **S1 (DB)**: contact email updated.
- **S2 (U)**: `contact_update`.

### N-005 update note
- **S1 (DB)**: notes appended.
- **S2 (U)**: kwargs.

### N-006 undo refuses
- **S1 (DB)**: confirmed sends → REFUSED, attempts intact.
- **S2 (U)**: at_risk query.

### N-007 undo flag-only refuses
- **S1 (DB)**: reached_out=1 no attempt → REFUSED.
- **S2 (U)**: OR flags in query.

### N-008 undo --confirm
- **S1 (DB)**: clears + WARNING.
- **S2 (U)**: delete attempts.

### N-009 undo no outreach
- **S1 (DB)**: no REFUSED, "Undone".
- **S2 (U)**: flag clears.

### N-010 attempts list
- **S1 (DB)**: rows listed.
- **S2 (U)**: query.

### N-011 status
- **S1 (DB)**: sent flags per contact.
- **S2 (U)**: output.

### N-012 person_keys canonical
- **S1 (U)**: vanity/email keys.
- **S2 (D)**: url variants.

### N-013 person_keys blank
- **S1 (U)**: empty → no key.
- **S2 (U)**: identifies nobody.

### N-014 person_keys variants
- **S1 (U)**: trailing slash/miniProfileUrn collapse.
- **S2 (D)**: variant corpus.

---

## O. State machine / recovery / cross-cutting

### O-001 legal advance
- **S1 (DB)**: advance valid.
- **S2 (U)**: transition table.

### O-002 illegal advance raises
- **S1 (DB)**: applied→described raises.
- **S2 (U)**: validation.

### O-003 applied no applied_at suspect
- **S1 (DB)**: seed applied w/o applied_at; `applied --suspects` flags.
- **S2 (U)**: query.

### O-004 recover wrong-applied
- **S1 (DB)**: set back to tailored.
- **S2 (U)**: re-verifiable.

### O-005 clear keeps identity
- **S1 (DB)**: reject → action flags gone, identity kept.
- **S2 (U)**: `clear_runtime_state`.

### O-006 shared lock
- **S1 (U)**: JidLock serializes.
- **S2 (DB)**: concurrent writes.

### O-007 per-job lock
- **S1 (U)**: second process waits/blocked.
- **S2 (DB)**: PID+TTL.

### O-008 stale lock reaped
- **S1 (U)**: TTL expiry.
- **S2 (DB)**: reacquire.

### O-009 URN trap
- **S1 (U)**: URN→non-URN field rejected.
- **S2 (F)**: read-back reject.

### O-010 reinterpreted→unverified
- **S1 (U)**: unsafe normalize → unverified.
- **S2 (F)**: no silent OK.

### O-011 risk-field split
- **S1 (U)**: prefilled kind+value surfaced.
- **S2 (F)**: dossier.

### O-012 dossier truth
- **S1 (U)**: apply_state `_role` marker.
- **S2 (DB)**: divergence resolves to dossier.

### O-013 submit race
- **S1 (U)**: two workers → one applied.
- **S2 (DB)**: atomic WHERE.

### O-014 pacing
- **S1 (U)**: delay between sends.
- **S2 (L)**: timing.

### O-015 fleet report
- **S1 (DB)**: per-job outcome.
- **S2 (U)**: accuracy calc.

### O-016 suspects clean
- **S1 (DB)**: 0 suspects.
- **S2 (U)**: query empty.

### O-017 shadow no mutation
- **S1 (DB)**: state snapshot equal.
- **S2 (U)**: isolation.

---

## P. Adversarial / curveballs (regression)

### P-001 C1 next refuses
- **S1 (U)**: BUTTON_GATE path.
- **S2 (F)**: no click side effect.

### P-002 C2 submit_clicked
- **S1 (U)**: GUARD path.
- **S2 (F)**: no page open.

### P-003 Antigua
- **S1 (U)**: `_pick_option` + country_words.
- **S2 (D)**: +1 ambiguity corpus.

### P-004 dialing map
- **S1 (D)**: coverage table.
- **S2 (U)**: round trip.

### P-005 select index
- **S1 (U)**: `_set_native_by_index`.
- **S2 (L)**: live select.

### P-006 captcha false-OK
- **S1 (U)**: "captcha" → captcha_required, no creds.
- **S2 (F)**: widget visible.

### P-007 creds unapproved
- **S1 (U)**: CRED_GUARD.
- **S2 (DB)**: domain_gate.

### P-008 undo disarm
- **S1 (DB)**: flag-only evidence → REFUSED.
- **S2 (U)**: query.

### P-009 blank identity
- **S1 (U)**: warning not block.
- **S2 (DB)**: no ALREADY_REACHED.

### P-010 non-target not success
- **S1 (F)**: target_url mismatch.
- **S2 (DB)**: repro.

---

## Q. `ji` orchestrator

### Q-001 status
- **S1 (DB)**: aggregate output.
- **S2 (U)**: per-job rows.

### Q-002 ready
- **S1 (DB)**: tailored+active filter.
- **S2 (U)**: `_ready_jids`.

### Q-003 verify --all
- **S1 (DB)**: no applied mutation.
- **S2 (F)**: verify all.

### Q-004 job dossier
- **S1 (U)**: handoff.json read.
- **S2 (DB)**: contents.

### Q-005 diff
- **S1 (U)**: profile vs dossier diff.
- **S2 (DB)**: listed.

### Q-006 audit
- **S1 (U)**: report generated.
- **S2 (DB)**: compliance flags.

### Q-007 fetch
- **S1 (U)**: pipeline invoked.
- **S2 (DB)**: jobs created.

### Q-008 apply passthrough
- **S1 (U)**: delegates.
- **S2 (DB)**: args.

### Q-009 submit passthrough
- **S1 (U)**: gates intact.
- **S2 (DB)**: one-shot.

### Q-010 shadow passthrough
- **S1 (U)**: shadow called.
- **S2 (DB)**: no submit.

### Q-011 answer
- **S1 (U)**: profile updated.
- **S2 (DB)**: persisted.

### Q-012 decisions
- **S1 (U)**: risk-unverified list.
- **S2 (DB)**: flags.

---

## R. Performance / robustness

### R-001 deadline
- **S1 (U)**: `_abort_timed_out` true after deadline.
- **S2 (DB)**: STATUS_TIMED_OUT.

### R-002 captcha bounded
- **S1 (U)**: wait_s = deadline math.
- **S2 (F)**: abort within budget.

### R-003 wait_for_fields
- **S1 (F)**: lazy fields appear.
- **S2 (U)**: timeout.

### R-004 validation errors parse
- **S1 (U)**: list returned, no raise.
- **S2 (D)**: message corpus.

### R-005 empty_required
- **S1 (F)**: blanks listed.
- **S2 (U)**: filter.

### R-006 isolated session
- **S1 (U)**: `_isolate_session` path.
- **S2 (F)**: context.

### R-007 detached frames
- **S1 (F)**: element detaches → next method.
- **S2 (U)**: try/except chain.

### R-008 registry fail safe
- **S1 (U)**: except → no custom widgets.
- **S2 (F)**: proceeds.

### R-009 ask_api off
- **S1 (U)**: allow() False → no API.
- **S2 (U)**: side_effect assert.

### R-010 bounded body read
- **S1 (U)**: large page ok.
- **S2 (F)**: memory bound.

---

## S. No-false-success invariants

### S-001 applied needs evidence
- **S1 (DB)**: no signal → not applied.
- **S2 (F)**: verify gating.

### S-002 no silent resend
- **S1 (DB)**: every transmit recorded.
- **S2 (U)**: flags + attempts.

### S-003 creds approved only
- **S1 (U)**: CRED_GUARD.
- **S2 (DB)**: domain_gate.

### S-004 no guessed country
- **S1 (U)**: needs_data not code.
- **S2 (D)**: corpus.

### S-005 no account under captcha
- **S1 (U)**: "captcha" no save.
- **S2 (DB)**: disk assert.

### S-006 no password promote on captcha
- **S1 (U)**: save not called.
- **S2 (DB)**: creds unchanged.

### S-007 vision second observer
- **S1 (U)**: called only ambiguous.
- **S2 (F)**: text-only ok.

### S-008 shadow never submits
- **S1 (DB)**: no applied.
- **S2 (U)**: isolation.

### S-009 force explicit
- **S1 (U)**: only with flag.
- **S2 (DB)**: no implicit.

### S-010 unrecoverable doc
- **S1 (U)**: doc exists + current.
- **S2 (DB)**: category list.

---

## Harness notes

- **Cheapest-first rule**: start with the `(U)` unit variant — most already have
  pinned tests (see TEST_CORPUS "verify" column). Add `(F)` fake-page variants
  for interaction logic, `(DB)` for state, `(L)` only for browser-dependent
  behavior.
- **Determinism**: fake-page variants avoid network/real ATS flakiness; keep one
  `(L)` smoke per area.
- **Mutation discipline**: `(DB)` variants must reset `schema._conn` in
  setUp/tearDown (see `_TempDBMixin`) — never write `~/.ji`.
- **New bug → pin**: any `FOUND` while running these should be fixed + added to
  `tests/` with the cheapest variant, then re-run the full suite.
