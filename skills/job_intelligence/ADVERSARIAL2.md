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

### A. Auto-login types saved passwords into an unauthenticated page — FIXED
`fill.py:991` `creds = get_creds(domain)` — `domain` is derived from the page
URL, and auto-login then types the real password into whatever login form is
present, switching to a "Sign In" form via heuristics (`fill.py:1011-1028`).

The threat: a **persuasive fake ATS** that passes DNS vetting (A4 checks the
host is public, not that it's the real ATS) and renders a login form would
receive the user's real email + password. The domain-key lookup does not
authenticate *which page* is receiving the credentials — it only keys on the
host string.

**Mitigation (applied):** a module-level `_domain_approved` guard
(`apply/act/fill.py:1001`, backed by `apply/common/domain_gate.py`) refuses to
type credentials into any domain that has not been explicitly approved
(`report.py domains approve|deny`). Mirrors F2's first-contact rule, extended
to credentials: a never-authenticated ATS domain gets no password typed until
human approval. Committed as `96ee679`.

### B. Lazy-loaded native `<select>` still can't reveal off-list options — FIXED
The phone-country field on CyberCoders: native `el.options` now has all 249,
and the country-match works when Canada IS present. The success path now
selects by **option INDEX from the authoritative `el.options` list**
(`apply/strategies/select.py` `_set_native_by_index`), bypassing the rendered
visible list entirely — this handles both lazy/truncated DOMs and options whose
`value` attribute differs from their text (country pickers: `value='CA'`,
`text='Canada (+1)'`). `native_setter` and `js_click` both use the index path;
`el.value=<text>` assignment is gone. Pinned by `NativeSelectStrategy` (5
tests). The Antigua guard is preserved: a known-but-not-loaded country still
returns no-match rather than falling to a bare-code pick.

### C. Low-severity residuals
- **Gmail search query** (`stage_emails.py:44`) is `shlex.split` from `.env` —
  env-sourced, not attacker-influenceable. Low.
- **Plaintext credential fallback** (`~/.ji/credentials.json`) — **HARDENED**:
  now opt-in (`JI_ALLOW_PLAINTEXT=1` or settings `allow_plaintext`). A silent
  keychain→plaintext downgrade refuses to write and says so; when allowed, the
  file is written with an owner-only ACL (`0o600`). Pinned by
  `PlaintextFallbackGate`.
- **`ji fetch` doc-vs-behavior** — fixed this pass.

## Recommendation
Fix **A** (credential-typing guard) first — it is the only verified-OPEN item
with real PII at stake. It extends the F2 first-contact rule to credentials:
a never-authenticated ATS domain gets no password typed until human approval.
