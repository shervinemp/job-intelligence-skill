# CAPTCHA + Login/Signup Automation Audit

Adversarial audit of the CAPTCHA, login, and account-creation surfaces. Every
finding verified against the code; the open gaps are honest.

## CAPTCHA gaps found & fixed

### C-C1. Runtime detector missed reCAPTCHA/hCaptcha — FIXED
`check_captcha` (apply/common/page_helpers.py) matched only Cloudflare/Turnstile
widgets (`iframe[src*="challenge"]`, `cf-browser`, `challenge`, `turnstile`
classes) and English body text. The probe-time detector (capabilities.py) already
matched `recaptcha` / `hcaptcha` / `captcha` iframes — so a form carrying a
Google reCAPTCHA or hCaptcha was flagged at probe time but the **runtime**
fill/submit loops never detected it, never paused for a human solve, and never
recorded `captcha_required`.

**Fix**: `check_captcha` now matches the same widget set (`iframe[src*="recaptcha"]`,
`iframe[src*="hcaptcha"]`, `iframe[src*="captcha"]`, `.g-recaptcha`, `.h-captcha`)
restricted to *visible* widgets (`offsetParent !== null`).

### C-C2. CAPTCHA on the auth form was recorded as a successful login — FIXED
During auto-login, a reCAPTCHA/hCaptcha blocking the sign-in form left
`_login_check` at `"uncertain"` — and the caller (fill.py) treated
`"uncertain"` as **assume-OK**, saving/promoting the password and returning
success. Same false-success in account creation: `_check_account_created`
returned `"uncertain"` on a captcha-blocked create form, and the caller saved
creds for an account that was never created.

**Fix**: both heuristics now return a distinct `"captcha"` signal (only when the
form is otherwise unresolved) and the callers record
`STATUS_CAPTCHA_REQUIRED` + hand off for a human solve — never save creds, never
promote a password. Pinned by `LoginWallCaptcha` (5 tests).

## Login/signup surfaces — status

| Surface | Status |
|---|---|
| Auto-login with password candidates + promotion | OK (guarded) |
| 2FA interstitial detection (`_login_check` → `2fa`) | OK |
| Credential domain gate (ADVERSARIAL #2-A, `_domain_approved`) | OK |
| Account creation with complexity-aware password generation | OK |
| "Account already exists" → try known password pool | OK |
| Guest-apply fallback (Workday et al.) | OK |
| Session-timeout popup dismissal (`handle_session_timeout`) | OK |
| Cookie-banner dismissal before auto-login | OK |

## Remaining open (honest)

- **hCaptcha/ARIA-only CAPTCHAs**: a CAPTCHA rendered as custom DOM with no
  recognizable class is now caught via `[role="captcha"]` and
  `aria-label*="captcha"` matching, plus multilingual body-text keywords
  (FR/ES/DE/RU/ZH/HI) — not exhaustive, but the widest net the detector
  currently casts. Residual blind spot: fully opaque ARIA-less widgets.
- **Per-domain CAPTCHA allow-list**: now supported — `captcha_skip_domains`
  in apply_policy.json skips (records captcha_required) only for listed
  domains, so one known-broken ATS no longer stalls every batch while other
  domains still wait for the human. Default: no domains listed.

## Test coverage added
- `LoginWallCaptcha`: login captcha → `captcha_required` (no cred save/promote);
  uncertain-then-captcha not assumed OK; `_login_check` / `_check_account_created`
  surface `"captcha"`; runtime detector sees reCAPTCHA widgets.
- `CaptchaDetectionExtra`: ARIA-role CAPTCHA detected; FR/RU captcha text
  detected; per-domain skip list honored (listed domain skips, unlisted waits);
  policy default has empty domain list.
- `PlaintextFallbackGate`: plaintext fallback refused by default; allowed via
  env; refused write leaves no file; allowed write persists.
