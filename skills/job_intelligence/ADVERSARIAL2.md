# Adversarial Probe #2 — verified findings

A second adversarial sweep, focused on surfaces not covered in INPUT_AUDIT /
FAILURE_MAP. Every finding below was verified against the live code.

## Verified-HARD findings (mitigated — keep the guards)

| Surface | Probe | Result |
|---|---|---|
| Outreach cross-job identity | Same person via two jobs / URL variants (`person_keys`, `_prior_outreach`) | **HARD** — canonical keys (li vanity / email), empty values identify nobody, connect→DM funnel preserved. |
| Runtime-rule domain scope | `_host_matches`: `greenhouse.io` vs `evilgreenhouse.io` / `greenhouse.io.evil.com` / `greenhouse.io.com` | **HARD** — all spoof variants return False; only same-host and true subdomains match. |
| Ask_api password-rule extractor | Sends only PAGE TEXT (attacker-controlled) to a local endpoint; a malicious page could inject a rule | **LOW** — affects only generated password complexity, not existing secrets; ask_api is local-only for image bytes (A3). |
| URN read-back | `urn:li:geo:...` from LinkedIn location typeahead | **HARD** — now refused (verify_failed, unverified) via the `__URN__:` guard. |

## Verified-OPEN finding (actionable)

### A. Auto-login types saved passwords into an unauthenticated page
`fill.py:991` `creds = get_creds(domain)` — `domain` is derived from the page
URL, and auto-login then types the real password into whatever login form is
present, switching to a "Sign In" form via heuristics (`fill.py:1011-1028`).

The threat: a **persuasive fake ATS** that passes DNS vetting (A4 checks the
host is public, not that it's the real ATS) and renders a login form would
receive the user's real email + password. The domain-key lookup does not
authenticate *which page* is receiving the credentials — it only keys on the
host string.

**Mitigation (proposed):** before typing any credential, verify the page is a
genuine login for the credentialed domain:
1. the current URL host must be an exact/subdomain match of the creds domain,
2. the page must have a password input AND an email/username input (a real
   sign-in form), not just a fake prompt,
3. and — the key guard — if the ATS domain has NEVER been successfully
   authenticated before (no prior logged-in session in the shared profile),
   require explicit approval before typing the password (mirrors F2's
   first-contact rule, extended to credentials).

### B. Lazy-loaded native `<select>` still can't reveal off-list options
The phone-country field on CyberCoders: native `el.options` now has all 249,
and the country-match works when Canada IS present. But a native `<select>` can
have the *rendered* DOM truncated while `el.options` is complete — the read-back
verified the selection, yet the field still shows rejected on that job. The
remaining gap: native selects whose visible options are lazy — the success path
needs a "select by exact option value from el.options" bypass of the visible
list. (Safety half done; success half open.)

### C. Low-severity residuals
- **Gmail search query** (`stage_emails.py:44`) is `shlex.split` from `.env` —
  env-sourced, not attacker-influenceable. Low.
- **Plaintext credential fallback** (`~/.ji/credentials.json`) — keyring is
  primary, but the fallback is a real on-disk surface. Worth hardening.
- **`ji fetch` doc-vs-behavior** — fixed this pass.

## Recommendation
Fix **A** (credential-typing guard) first — it is the only verified-OPEN item
with real PII at stake. It extends the F2 first-contact rule to credentials:
a never-authenticated ATS domain gets no password typed until human approval.
