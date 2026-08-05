# Job Flow Map — flow and instructions

This document describes what happens to information as it moves through the
job-application workflow, and where judgement is exercised. It has no grouping,
no stages, and no technical language: each step stands alone and can be
re-arranged however you like. Read it as a flat list and re-assemble it into
whatever shape fits your design.

Each step uses a fixed template:

- **In** — what this step needs
- **Do** — what happens here
- **Out** — what this step produces
- **Decide** — the judgement made here, and on what basis
- **Observe** — what is independently checked (rather than assumed)
- **If it can't tell** — what happens when this step cannot determine its result

---

**1. A message arrives**
- In: a message waiting to be looked at.
- Do: the message is received and held.
- Out: a held message.
- Decide: none.
- Observe: none.
- If it can't tell: not applicable.

**2. The message is screened as a possible job lead**
- In: a held message.
- Do: who sent it, its heading, and how it has been filed are compared against the usual signs of a job notice (typical senders, typical headings, known filing labels).
- Out: the message is marked "looks like a job" or "does not".
- Decide: could this message contain a job opening? Basis: how closely it matches the usual signs.
- Observe: none.
- If it can't tell: borderline messages are skipped rather than added to the working list.

**3. A destination is pulled out of the message**
- In: a message marked as a possible job lead.
- Do: the message text is scanned for a destination the job lives at; each one is noted along with a few words of surrounding context.
- Out: one or more candidate destinations, each with its context.
- Decide: none.
- Observe: none.
- If it can't tell: a message with no usable destination produces nothing and is set aside.

**4. A destination is judged safe to follow**
- In: a candidate destination.
- Do: the destination is checked — it must be an ordinary, public one; it must not carry hidden account details embedded in it; and it must point somewhere reachable from the outside rather than a private or internal place.
- Out: the destination is accepted, or refused with a reason.
- Decide: may the flow follow this destination? Basis: it is ordinary, public, has no embedded account details, and is reachable.
- Observe: what the destination actually points to.
- If it can't tell: the destination is refused — anything that cannot be checked is not followed.

**5. A destination is checked for prior knowledge**
- In: an accepted destination.
- Do: a stable way of recognising the same posting is worked out from the destination and compared against every posting we already hold.
- Out: verdict "new" or "seen before".
- Decide: have we already come across this posting? Basis: the recognition matches something we hold.
- Observe: none.
- If it can't tell: treated as new (a true repeat is caught later by a stronger check).

**6. A candidate is given a category**
- In: a new, accepted destination.
- Do: the candidate is placed into one of the known working categories and given a starting status.
- Out: a categorised candidate in the working set.
- Decide: which category does this candidate belong to? Basis: a reader's judgement, since the destination alone gives little signal.
- Observe: none.
- If it can't tell: the candidate cannot be admitted until a category is chosen.

**7. A candidate is accepted, set aside, or flagged**
- In: a categorised candidate.
- Do: the candidate is admitted to the active working list, set aside as not wanted, or flagged as possibly blocked.
- Out: the candidate's status is active, set-aside, or flagged.
- Decide: is this candidate worth pursuing? Basis: category, any available title/company hints, and general relevance rules.
- Observe: none.
- If it can't tell: set aside (can be revisited) rather than assumed good.

**8. The posting is fetched**
- In: an active candidate's destination.
- Do: the posting living at the destination is brought back.
- Out: the posting's content, or a reason it failed.
- Decide: did bringing it back succeed? Basis: how much real content came back.
- Observe: the content actually returned.
- If it can't tell: bringing it back is tried again a bounded number of times, then the candidate is marked failed.

**9. The posting is checked for a sign-in barrier**
- In: the fetched content.
- Do: the content is scanned for signs that signing in is required before it can be read.
- Out: verdict "open" or "behind a sign-in".
- Decide: can the posting be read without signing in? Basis: presence of sign-in signs in the content.
- Observe: none.
- If it can't tell: treated as open, but noted; a person can flag it explicitly later.

**10. Posting content becomes a readable description**
- In: fetched content from an open posting.
- Do: surrounding clutter is removed and the substantive posting text is tidied up.
- Out: a clean description.
- Decide: none.
- Observe: none.
- If it can't tell: near-empty output is treated as if the bringing-back had failed.

**11. Key facts are pulled from the posting**
- In: the fetched content.
- Do: the posting's own stated details are read where available — job title, company, location, and salary range.
- Out: fact candidates for title, company, location, salary.
- Decide: none.
- Observe: the facts as stated by the posting itself.
- If it can't tell: missing facts stay empty; they are never invented.

**12. The posting is checked for a near-duplicate**
- In: description, title, and company.
- Do: the title+company pair is compared against known postings with a spelling-tolerant match.
- Out: verdict "unique" or "duplicate of a known posting".
- Decide: is this the same posting we already hold under a different destination? Basis: how similar the title and company are.
- Observe: none.
- If it can't tell: treated as unique.

**13. The description is reviewed for fit**
- In: description, facts, and any context notes.
- Do: the substance is read and judged against the acceptance rules (preferred location, remote friendliness, seniority, role fit).
- Out: verdict "fit" or "not fit".
- Decide: is this posting worth taking forward? Basis: reading the actual content against the criteria.
- Observe: none.
- If it can't tell: the description is surfaced for a reader to judge.

**14. The job is marked described or set aside**
- In: fit verdict, facts, description.
- Do: accepted jobs are marked described (facts kept); rejected ones are set aside with a reason.
- Out: job status is described or set-aside.
- Decide: none (carries out the fit verdict).
- Observe: none.
- If it can't tell: not applicable.

**15. A tailored document is drafted for a described job**
- In: description, facts, and the person's own record.
- Do: a job-specific version of the resume (and optionally a cover letter) is written to match the posting, using only facts from the person's record.
- Out: a tailored resume draft and cover draft.
- Decide: none (this is drafting).
- Observe: none.
- If it can't tell: drafting is tried again (waiting out rate limits and temporary troubles); repeated failure marks the job failed.

**16. Every claim in the draft is traced back to the record**
- In: the tailored draft and the person's record.
- Do: each company, title, date, degree, qualification, and concrete figure in the draft is checked against the person's actual record; bare assertions of credentials, clearances, licences, team sizes, and large numbers are specifically hunted for.
- Out: a list of traced claims, unfounded claims, and mismatches.
- Decide: is every claim justifiable by the person's real history? Basis: whether the claim appears in the record.
- Observe: the person's record (the source of truth), not the draft's own words.
- If it can't tell: the claim is treated as unfounded — always lean toward review, never toward acceptance.

**17. Unfounded claims are resolved**
- In: the tracing report.
- Do: each unfounded claim is either corrected in the draft, added to the record as a real fact, or explicitly accepted after review.
- Out: a clean draft (or one with knowingly accepted exceptions).
- Decide: is this claim a real fact (→ add to record), a mistake (→ fix draft), or an accepted exception (→ approve)? Basis: a reader judging against the person's real history.
- Observe: none.
- If it can't tell: the job stays blocked until the claim is handled.

**18. The draft is turned into ready-to-send documents**
- In: the cleaned draft.
- Do: the resume and cover are turned into finished documents, with safe, collision-free names.
- Out: finished documents ready to attach.
- Decide: none.
- Observe: none.
- If it can't tell: a failure here blocks the job.

**19. The job is marked ready to apply**
- In: finished documents and a clean tracing report.
- Do: the job's status advances to "has a ready, trustworthy document".
- Out: job status is ready-to-apply.
- Decide: none.
- Observe: none.
- If it can't tell: not applicable.

**20. The entry point into the application is located**
- In: a ready job and its destinations.
- Do: the pages are examined to find the actual control that starts applying.
- Out: an identified entry action.
- Decide: none.
- Observe: the control actually present on the screen.
- If it can't tell: flagged as needing manual navigation.

**21. The kind of application flow is determined**
- In: the located entry point and the destination's pattern.
- Do: the flow is classified — a form that opens in place, a direct employer form, an outside service, or already applied.
- Out: a flow kind.
- Decide: which known family does this flow belong to? Basis: the destination's pattern and the screen's structure.
- Observe: none.
- If it can't tell: falls to the deepest investigation path.

**22. The journey to the form is carried out safely**
- In: flow kind and entry point.
- Do: the flow moves to the application form, following whatever path the form takes and handling sign-in walls along the way.
- Out: an open form, or a sign-in/blocked state.
- Decide: none.
- Observe: the final screen reached and its state.
- If it can't tell: a sign-in wall is surfaced for a person to sign in manually, keeping the signed-in state for later use.

**23. The form's fields are listed**
- In: the form's screen.
- Do: all the places that accept an entry are located across the screen and its layered sections; each is given a label, an entry kind, and a required/optional marker.
- Out: a list of fields.
- Decide: none.
- Observe: the fields as they exist on the screen.
- If it can't tell: fields that can't be labelled are flagged for deeper probing.

**24. Each field is given a meaning**
- In: the field list and the screen's structure.
- Do: each field's label, name, and surrounding text is interpreted into a purpose (name, contact details, location, legal question, consent choice, and so on).
- Out: a mapping from each field to its meaning.
- Decide: what is this field asking for? Basis: the label, the hints, and the context.
- Observe: none.
- If it can't tell: the field is marked uninterpreted (a candidate for a reader, or for learning its meaning).

**25. An answer is looked up in the person's record**
- In: a field's meaning and the person's record.
- Do: the record is searched for a fact matching the meaning.
- Out: an answer, or "not in record".
- Decide: none (this is a lookup).
- Observe: none.
- If it can't tell: move to the next source of answers.

**26. A previously-learned answer is looked up**
- In: a field's meaning (with its context).
- Do: past verified answers for the same meaning in the same context are recalled.
- Out: an answer, or "not learned".
- Decide: none (this is a lookup).
- Observe: none.
- If it can't tell: move to the next source.

**27. A safe default is considered**
- In: a field's meaning.
- Do: for meanings with a universally safe answer (marketing consent, alerts, opt-in switches), the safe value is used.
- Out: an answer, or "no default".
- Decide: is the safe default appropriate here? Basis: the meaning is a privacy-conservative kind only.
- Observe: none.
- If it can't tell: move on.

**28. An unanswered field becomes an open decision**
- In: a field with no answer found above.
- Do: the field is recorded as needing a decision, with the evidence gathered (interpreted meaning, options seen).
- Out: an open decision item.
- Decide: none (this creates the decision).
- Observe: none.
- If it can't tell: not applicable.

**29. A supplied answer is entered into the field**
- In: a field and an answer (from the record, learned history, safe default, or a reader).
- Do: the value is entered using the interaction the control needs (typing, choosing, selecting, ticking, attaching a document).
- Out: a filled field.
- Decide: none.
- Observe: none.
- If it can't tell: the failure is recorded with the attempted value and the reason.

**30. The value is read back from the screen**
- In: a filled field.
- Do: the field's current state is read back through several ways in turn (what was typed, what is selected, what is visibly shown).
- Out: an observed value, or nothing.
- Decide: none.
- Observe: the screen's actual state — this is the whole point.
- If it can't tell: the read-back ways return nothing.

**31. The read-back is compared to the intended answer**
- In: the observed value and the intended answer.
- Do: the observed value is matched against the answer — including the tricky case where the observed text merely *contains* the answer (for example a country calling number sitting inside a country name).
- Out: verdict "confirmed", "not confirmed", or "only repeats what was supplied".
- Decide: did the value genuinely land as intended? Basis: the observation must be independent proof, not a restatement of what was typed in.
- Observe: the comparison itself.
- If it can't tell: a repeated or empty read-back is treated as NOT confirmed.

**32. The field is marked verified or unverified**
- In: the comparison verdict.
- Do: confirmed fields are stamped verified (with the observed value); everything else is stamped unverified.
- Out: a verification status for each field.
- Decide: none (carries out the comparison verdict).
- Observe: none.
- If it can't tell: not applicable.

**33. Unverified fields in sensitive categories are escalated**
- In: the verification statuses.
- Do: unverified fields whose meaning touches identity, legal status, location, salary, or relocation are marked as blocking; other unverified fields are marked as worth a look but not blocking.
- Out: a blocking set and a review set.
- Decide: is this field in the sensitive category? Basis: its meaning.
- Observe: none.
- If it can't tell: treated as blocking — lean toward caution.

**34. Filled values are checked against each other**
- In: all filled values.
- Do: values are checked for contradictions between them (for example city vs country, or a visa answer vs an eligibility answer).
- Out: a set of contradictions, or none.
- Decide: do any filled values contradict each other? Basis: logical pairings of meanings.
- Observe: none.
- If it can't tell: unresolved pairs are treated as suspicious.

**35. The overall state of the form is summarised**
- In: per-field statuses and contradictions.
- Do: counts are produced — filled, failed, skipped-optional — with the guarantee that the three add up to the total number of fields exactly once each.
- Out: a one-line summary.
- Decide: none.
- Observe: none.
- If it can't tell: unclassifiable fields count as failed, never as filled.

**36. A decision to submit is reached**
- In: the summary, the contradictions, the verification statuses, and the record of prior attempts.
- Do: submission is gated — blocking fields, contradictions, or a previously recorded attempt each prevent a new submit.
- Out: "allowed to submit" or "not allowed".
- Decide: may we submit? Basis: no blocking fields, no contradictions, no prior attempt, and the current mode explicitly permits a real submission.
- Observe: the record of prior attempts.
- If it can't tell: block. A missing record of permission never opens the gate.

**37. The submit is performed**
- In: permission and the form.
- Do: the submit action is carried out; the attempt is recorded *before* the action, so that a crash cannot cause the action to be performed twice.
- Out: a submitted form and a recorded attempt.
- Decide: none.
- Observe: the attempt record (written before the action).
- If it can't tell: a crash mid-action leaves the record; the next run investigates, never acts again.

**38. The outcome is observed**
- In: the screen state after submitting.
- Do: the screen is scanned for success signs (confirmation text, the screen changing, the form disappearing), then for error signs (validation messages).
- Out: a success or error verdict.
- Decide: did the submission land? Basis: observed signs on the screen.
- Observe: the screen state after the action.
- If it can't tell: deeper investigation (including a visual check) is triggered.

**39. An uncertain outcome is investigated, not repeated**
- In: an uncertain verdict.
- Do: all evidence is gathered and reviewed — signs, screen state, visual check — and the outcome is classified.
- Out: a confident outcome, or a review case.
- Decide: what actually happened? Basis: the full evidence set.
- Observe: the gathered evidence.
- If it can't tell: the job is held for review — never submitted again.

**40. The job is marked applied**
- In: a confident success verdict.
- Do: the job's status advances to applied, with the outcome recorded.
- Out: job status is applied.
- Decide: none.
- Observe: none.
- If it can't tell: not applicable.

**41. People connected to the job are discovered**
- In: the job, its company, and the posting.
- Do: candidate contacts are gathered — recruiters from the posting, team members from the company's people list (filtered to the relevant team), and existing connections at the company.
- Out: a set of candidate contacts.
- Decide: none (this is gathering).
- Observe: the people actually found.
- If it can't tell: an empty set is recorded as such.

**42. Each contact is checked for prior outreach**
- In: the candidate contacts.
- Do: each person is matched against all prior outreach using a stable identity (a standard form of their address or profile name) — blank details match nobody.
- Out: per-person "not yet reached" or "already reached".
- Decide: has this specific person already been contacted? Basis: matching the stable identity against history.
- Observe: the outreach history.
- If it can't tell: blank identity matches nothing — it never wrongly blocks a stranger, and never misses a real repeat.

**43. A contact is chosen**
- In: not-yet-reached contacts and their evidence.
- Do: one person is selected for outreach.
- Out: a chosen contact.
- Decide: whom do we approach? Basis: relevance of their role and the evidence.
- Observe: none.
- If it can't tell: the choice is surfaced to a reader.

**44. A message is written**
- In: the chosen contact and the job context.
- Do: a tailored message is drafted (a written note, a direct message, or a connection note), grounded only in real facts.
- Out: a draft message.
- Decide: none.
- Observe: none.
- If it can't tell: drafting falls back to a reader.

**45. The message is sent**
- In: the draft, the chosen contact, and the per-person safety record.
- Do: the message is sent and the sending is recorded.
- Out: an attempt record and a sent mark.
- Decide: none.
- Observe: the sending's own confirmation.
- If it can't tell: an unconfirmed send is recorded as pending — it is never silently sent again.

**46. An unconfirmed send is verified**
- In: a pending attempt.
- Do: the recipient's inbox is checked for the message.
- Out: verdict "delivered" or "not seen".
- Decide: did it actually go out? Basis: observing the recipient's inbox.
- Observe: the inbox state.
- If it can't tell: it stays pending until a person confirms.

**47. The person's record is checked for readiness**
- In: the person's record.
- Do: completeness is assessed — the facts needed to answer common questions (contact details, work history, education, legal answers) must exist; gaps are listed.
- Out: a readiness report (complete, or with gaps).
- Decide: is the record complete enough to run a whole batch? Basis: coverage of the known question kinds.
- Observe: none.
- If it can't tell: the gaps are surfaced, and the batch is not run blind.

**48. The finished documents are attached to the application**
- In: the finished documents and the application form.
- Do: the resume (and cover) are attached where the form asks for them.
- Out: an attached document.
- Decide: none.
- Observe: the form shows the document attached.
- If it can't tell: the failure is recorded and the job stops.

**49. A multi-part form is advanced through**
- In: a partially filled form with more parts ahead.
- Do: the flow moves to the next part of the form, and finally to the review screen before submitting.
- Out: the next part, or the review screen.
- Decide: none.
- Observe: the form's position has changed.
- If it can't tell: the flow stops, keeping the current state.

**50. A supplied answer is remembered for the future**
- In: a field's meaning and the answer that worked (especially one supplied by a reader).
- Do: the answer is saved into learned history, scoped to the context it came from.
- Out: a learned entry.
- Decide: none.
- Observe: none.
- If it can't tell: the answer is not remembered — no harm beyond repeating the question later.

**51. The current run is compared with the previous run**
- In: this run's results and the previous run's results for the same job.
- Do: each field's outcome is compared — was something filled before that now fails? Did a value change?
- Out: a difference report (improved / regressed / unchanged).
- Decide: is anything that used to work now broken? Basis: field-by-field comparison.
- Observe: none.
- If it can't tell: treated as a regression — lean toward caution.

**52. An outreach message's facts are traced back to the record**
- In: the drafted message and the person's record.
- Do: concrete facts in the message (dates, numbers, names, claims) are checked against the record, the same way the resume is checked.
- Out: a list of traced and untraced facts.
- Decide: are the message's facts justifiable? Basis: the fact appears in the record.
- Observe: the record.
- If it can't tell: the fact is treated as untraced — fix or approve before sending.

**53. A failed step is retried**
- In: a step that failed (bringing back a posting, drafting, filling).
- Do: the step is attempted again a bounded number of times, waiting out temporary trouble.
- Out: success, or a final failure with the reason.
- Decide: none (the retries are bounded).
- Observe: none.
- If it can't tell: the final failure is recorded with its reason.

**54. Set-aside postings are re-examined later**
- In: postings previously set aside as "could not tell if closed".
- Do: they are looked at again to see whether the uncertainty has resolved.
- Out: verdict "now closed" or "now applicable".
- Decide: did the situation change since? Basis: re-observation of the posting.
- Observe: the posting again.
- If it can't tell: stays set aside.

---

## Loose standing particles

These are not steps in the sequence — they are standing surfaces and invariants
that the steps act against. They are listed loosely; rework them however your
design needs.

**L1. The decision inbox (a surface, not a step)**
Every open decision (28) is collected into one place, grouped by who must
resolve it, with the evidence attached. The rest of the flow reads from and
writes to this surface; nothing else carries decisions around.

**L2. The evidence trail (a surface, not a step)**
Every step that observes (has an Observe) writes its observation to a shared,
per-job record that accumulates over time. The regression comparison (51) and
any later review read from this trail. A step that observes nothing has nothing
to contribute to it.

**L3. The one-shot guard (an invariant, not a step)**
Certain actions — submitting, sending a message, connecting with a person — may
be performed at most once per job/person. The guard is checked before the action
and set *before* the action happens, so a crash cannot double-fire. It is only
cleared by a deliberate, explicit override after a person confirms the earlier
attempt truly failed. Every such action in the flow carries this invariant.

**L4. State persistence (an invariant, not a step)**
Progress survives interruptions: what has been fetched, filled, verified, sent,
and decided is written down continuously, so a crash resumes rather than
restarts, and so the one-shot guard and the evidence trail survive process
boundaries. No action is assumed durable just because it happened in memory.

**L5. Archival (a standing action)**
When a job is fully done (applied, or set aside permanently), its working state
is moved aside so the active list stays clean — but its evidence trail is kept,
not deleted, so history remains reviewable.

**L6. Non-transmission during practice (an invariant)**
When a run is meant only to check the flow (shadow/practice mode), the final
action of any guarded step is suppressed — filling, verifying, and recording
happen, but submitting/sending does not. This is the same pipeline running with
one invariant inverted.

---

## How to use this map

- **No ordering is fixed.** The steps above can be sequenced however your design
  requires. For example, people discovery (41–46) can run in parallel with the
  application flow (20–40); an unanswered field (28) can trigger a reader while
  the form is still being filled, not only afterwards. A few steps are
  cross-cutting by nature and may be placed anywhere: readiness (47) runs before
  a batch, learning (50) fires whenever a supplied answer works, the regression
  comparison (51) runs before submit, retry (53) wraps any failing step, and
  re-examination (54) happens on a later pass.
- **The standing particles (L1–L6) are not sequenced** — they are the surfaces
  and invariants the sequenced steps act against. The decision inbox (L1) and
  evidence trail (L2) collect what steps produce; the one-shot guard (L3) and
  state persistence (L4) constrain every guarded action; archival (L5) tidies up;
  practice mode (L6) is the same flow with the final action suppressed.
- **Decide is where a judgement lives.** If you want a judgement made by an
  automated part versus by a person, that is your placement choice — it is not a
  property of the step.
- **Observe is what makes a claim trustworthy.** If a step has no Observe, then
  anything downstream that relies on it is trusting a self-report. Design your
  system so claims rest on steps that observe.
- **"If it can't tell" is the fail-mode contract.** It tells you what the step
  does when it cannot determine its result. Choose fail-modes that are loud and
  hand off to a person — never silent and assume-success.
