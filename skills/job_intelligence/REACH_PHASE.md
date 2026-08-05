# REACH Phase — Contact Discovery & Outreach Pipeline

**Design bible for adding Gmail send, LinkedIn DM, company connections, and team contact discovery to the Job Intelligence Pipeline.**

---

## 1. Objective

Extend the pipeline so that for each job posting we can:
1. **Discover contacts** — recruiters, team members, employees at the company, and my existing connections there.
2. **Reach out** — send tailored emails via Gmail and/or LinkedIn DMs to initiate conversations alongside (or before) the formal application.

This turns the pipeline from a one-directional "apply and wait" flow into a **multi-channel outreach system**.

---

## 2. Design Ethos

This phase follows the project's existing conventions:

- **LLM-as-operator**: The LLM reads pipeline output, decides who to contact, crafts message content, and runs commands. The pipeline automates discovery and delivery.
- **CLI-first**: Everything exposed as CLI commands with structured output for LLM consumption.
- **No per-platform Python code**: Platform-specific selectors live in registry YAML or JS probes. The engine has no per-platform branches.
- **One-shot safety**: Submit/message is one-shot. Guard flags prevent double-sending. `--force` clears guards after human verification.
- **Probe cascade for unknowns**: Use capability scans + observation store for LinkedIn page variation (premium vs free, desktop vs mobile browser).
- **Data before action**: Always check the DB (contacts, companies tables) before making API/network calls.
- **Sequential pipeline**: One job at a time. No parallelism.

---

## 3. Pipeline Integration

### 3.1 New Pipeline Stage: `reach`

The existing stages are: `extracted` → `described` → `tailored` → `applied`

We introduce a parallel contact-discovery and outreach track that operates alongside the apply flow:

```
extracted ──→ described ──→ tailored ──→ applied
                   │              │
                   ▼              ▼
             contact_discovery  reach_out
                   │              │
                   ▼              ▼
              contacts DB     events/contacts
```

**Contact discovery** fires automatically after a job reaches `described` (we know company + team from JD). It populates the `contacts` table.

**Reach-out** fires after `tailored` (CV exists → can reference it in messages). It updates `contacts.reached_out`, creates events.

### 3.2 New CLI: `reach.py`

```
reach.py discover <jid>           Auto-discover all contacts for a job
reach.py list <jid>                Show discovered contacts
reach.py email <jid> [--contact N] [--dry-run]  Send email to a contact
reach.py message <jid> [--contact N] [--dry-run] Send LinkedIn DM
reach.py retry <jid>               Retry failed contact discovery
reach.py connect <jid> [--contact N]  Send LinkedIn connection request
reach.py undo <jid>                Reset contact state
```

### 3.3 New DB Schema Additions

#### Enhance `contacts` table (existing):
```sql
-- Already has: id, job_id, company_id, name, role, email, linkedin_url,
--              notes, reached_out, created_at
-- Add:
ALTER TABLE contacts ADD COLUMN source TEXT DEFAULT '';       -- 'recruiter_auto' / 'team_search' / 'my_connection' / 'manual'
ALTER TABLE contacts ADD COLUMN confidence REAL DEFAULT 0.0;  -- 0.0-1.0 how sure we are this is the right person
ALTER TABLE contacts ADD COLUMN message_sent INTEGER DEFAULT 0;
ALTER TABLE contacts ADD COLUMN email_sent INTEGER DEFAULT 0;
ALTER TABLE contacts ADD COLUMN last_contacted_at TEXT;
ALTER TABLE contacts ADD COLUMN profile_picture_url TEXT DEFAULT '';
ALTER TABLE contacts ADD COLUMN headline TEXT DEFAULT '';      -- LinkedIn headline
ALTER TABLE contacts ADD COLUMN connection_degree TEXT DEFAULT ''; -- 1st, 2nd, 3rd
```

#### New table: `contact_attempts`
```sql
CREATE TABLE IF NOT EXISTS contact_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK(channel IN ('email', 'linkedin_message', 'linkedin_connect')),
    direction TEXT NOT NULL DEFAULT 'outbound' CHECK(direction IN ('outbound', 'inbound')),
    subject TEXT DEFAULT '',
    body TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','failed','opened','replied')),
    message_id TEXT DEFAULT '',       -- Gmail message ID or LinkedIn conversation ID
    error TEXT DEFAULT '',
    sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

#### New column on `jobs`:
```sql
ALTER TABLE jobs ADD COLUMN team_name TEXT DEFAULT '';        -- Extracted from JD by LLM
ALTER TABLE jobs ADD COLUMN contact_discovered INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN outreach_attempted INTEGER DEFAULT 0;
```

#### New table: `company_connections`
```sql
CREATE TABLE IF NOT EXISTS company_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT REFERENCES companies(id) ON DELETE CASCADE,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    linkedin_url TEXT DEFAULT '',
    headline TEXT DEFAULT '',
    connection_degree TEXT DEFAULT '1st',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

---

## 4. Component 1: Gmail Send

### 4.1 Scope

Extend `gmail_cli.py` with the ability to **send** emails. Currently it only has `gmail.readonly` scope. We add a second auth flow for `gmail.send` scope.

### 4.2 Design Decision

**Chosen: Extend existing `gmail_cli.py`** (per user decision).

Add a second token file with `gmail.send` scope alongside the existing read-only token. The existing `--services` flag on `auth add` already hints at multi-service support — we make it actually work.

### 4.3 New Scope: Send vs Compose

| Scope | Permission | Why |
|-------|-----------|-----|
| `gmail.send` | Send messages only; cannot read | Ideal for sending — least privilege. Can't accidentally read inbox. |
| `gmail.compose` | Read, compose, send | Too broad for sending only. We use `gmail.send`. |

We store a **separate send-scope token** at `~/.config/gmail-cli/tokens/<email>.send.json`.

### 4.4 New Commands

```
gmail-cli email send <to> <subject> <body-file> [--cc] [--bcc]
gmail-cli email send <to> <subject> --body "inline text"
gmail-cli auth add <email> --services gmail.send   # send-only scope
gmail-cli auth add <email> --services gmail        # existing read-only
gmail-cli auth list                                 # shows send vs read tokens
```

### 4.5 Implementation

**New file**: Add to `gmail_cli.py`:
- `SEND_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]`
- `_get_send_service(email)` — loads `{email}.send.json` token
- `_cmd_email_send(to, subject, body, cc=None, bcc=None)` — builds RFC 2822 message, calls `service.users().messages().send()`
- `_build_mime(to, subject, body, cc, bcc)` — constructs MIME message with utf-8 encoding

**Key detail**: The `.send` scope uses `messages.send` API, not `messages.insert`. We construct the raw MIME message ourselves using `email.mime.text`.

**Flow**:
1. LLM writes email body to a temp file (or provides inline)
2. `gmail-cli email send` encodes as MIME
3. Calls Gmail API `users.messages.send`
4. Returns message ID on success

---

## 5. Component 2: LinkedIn DM

### 5.1 Approach Analysis

Per user question — here is the comparison:

| Approach | Reliability | Speed | Complexity | Premium Needed |
|----------|-------------|-------|-----------|---------------|
| **A: Messaging URL `/messaging/thread/new/`** | Medium — URL format changes, requires profile ID (not username) | Fast (1 page load) | Medium — need to extract recipient URN | No |
| **B: Profile page → Message button** | High — stable button selector, works with username or ID URLs | Slower (2 page loads) | Low — just click | No for 1st deg; Message button may not appear for 2nd/3rd |
| **C: GraphQL API** | Low — brittle, TOS risk, needs auth tokens | Fastest | High — reverse-engineering | No |

**Decision: Hybrid of A + B, with A as primary, B as fallback.**

**Strategy**:
1. **Primary (A)**: Navigate directly to `https://www.linkedin.com/messaging/thread/new/?recipient={profileId}`. This opens the messaging compose view. We detect if the page loaded correctly (compose box visible) and send the message. **Pros**: Single page load, direct to compose.
2. **Fallback (B)**: If A fails (compose box not visible, redirected), navigate to profile page `https://www.linkedin.com/in/{username}/`, click the "Message" button, then fill the overlay compose box.
3. **Detection**: After both attempts, check for success indicators (message sent confirmation, or messages list showing new thread).

### 5.2 Selectors (to verify via inspection)

These are the expected selectors. They MUST be verified by live inspection (`act --inspect` equivalent on LinkedIn messaging pages):

**Profile page**:
- Message button: `button[aria-label="Message"]`, or `a[aria-label="Message"]`
- Connect button (if not connected): `button[aria-label="Connect"]`, or `button:has-text("Connect")`
- More button: `button[aria-label="More actions"]`

**Messaging compose**:
- Compose URL pattern: `https://www.linkedin.com/messaging/thread/new/?recipient={urn}`
- Recipient URN format: `urn:li:person:{profileId}` where profileId is the numeric portion from profile URL
- Message input: `div[role="textbox"][aria-label*="message" i]` or `div.msg-form__contenteditable`
- Send button: `button[aria-label="Send"]` or `button.msg-form__send-button`
- Character count: `.msg-form__remaining-char-count`

**After send**:
- Success signals: message appears in thread, or button changes to "Sent"
- Error signals: "Message not sent. Try again", "This member has chosen to only receive messages from people they may know"

### 5.3 Premium vs Non-Premium Handling

| Scenario | What happens | How pipeline handles |
|----------|-------------|---------------------|
| 1st degree (any account) | Message button visible, DM sent | Standard flow |
| 2nd/3rd degree (no premium) | Message button hidden, "Connect" button shown | Pipeline emits `CONNECT_REQUIRED` signal, can auto-send connection request |
| 2nd/3rd degree (with premium/InMail) | InMail button or "Message" button visible | Pipeline uses InMail composer (same selectors, different API) |
| Open profile (no premium) | "Message" button may still appear | Same as 1st degree |
| "Message" button → premium upsell | Button redirects to premium page | Detect redirect, emit `PREMIUM_REQUIRED` |

**Detection probe** (JS injected into page):
```javascript
() => {
  const msgBtn = document.querySelector('button[aria-label="Message"], a[aria-label="Message"]');
  const connectBtn = document.querySelector('button[aria-label="Connect"], a[aria-label="Connect"]');
  const inmailLink = document.querySelector('a[aria-label*="InMail"], a[href*="premium"]');
  const composeBox = document.querySelector('div[role="textbox"][aria-label*="message" i]');
  return {
    hasMessageButton: !!msgBtn && msgBtn.offsetParent !== null,
    hasConnectButton: !!connectBtn && connectBtn.offsetParent !== null,
    hasInmailOption: !!inmailLink,
    hasComposeBox: !!composeBox,
  };
}
```

### 5.4 Implementation

New module: `lib/linkedin_messaging.py`

```python
def can_message(ctx, profile_url):
    """Check if we can DM this person. Returns capability dict."""

def send_message(ctx, profile_url, message_body):
    """Send a LinkedIn DM. Returns success/error + conversation URL."""

def send_connect_request(ctx, profile_url, note=""):
    """Send a connection request with optional note."""
```

Uses the shared `chrome_manager.connect()` pattern, same as `linkedin.py` and `enrich.py`.

---

## 6. Component 3: Contact Discovery System

### 6.1 Discovery Sources

For each job, we discover contacts from these sources in order:

#### Source A: Recruiter from job page (existing, enhanced)
Already implemented in `linkedin.py:_RECRUITER_JS`. Extracted from the LinkedIn job detail page. We:
- Save to `contacts` table with `source='recruiter_auto'`
- Enrich with role, email if visible
- This runs during `linkedin.py` scrape AND during `enrich.py` fetch (for non-LinkedIn jobs)

#### Source B: Team members from company LinkedIn (NEW)
When the LLM or job posting mentions a team/department (e.g., "AI/ML team", "product team"):
1. LLM extracts team name from JD during enrich → stored in `jobs.team_name`
2. Pipeline searches LinkedIn company page for people in that team:
   - URL: `https://www.linkedin.com/company/{company-slug}/people/`
   - Scroll through employee list
   - For each employee card, extract: name, role, profile URL, headline
   - Filter by team keywords (from JD or LLM-extracted)
3. Save as contacts with `source='team_search'`

#### Source C: My connections at the company (NEW)
Find any 1st-degree connections who work at the target company:
1. **Primary**: LinkedIn search URL:
   ```
   https://www.linkedin.com/search/results/people/?network=%5B%22F%22%5D&currentCompany={companyId}
   ```
   `network=["F"]` = 1st degree only. `currentCompany` filters by employer.
2. **Fallback**: Navigate to company page, check for "X connections work here" section:
   - Selector: `a[href*="connections"]` or section with text containing "connections work here"
   - Click to expand, extract names + profile URLs
3. Save as contacts with `source='my_connection'`

#### Source D: Email lookup via LLM (NEW)
For discovered contacts with a name + company but no email:
1. Pipeline checks if we have an LLM endpoint configured (`LLM_API_URL`)
2. If yes, asks the LLM to suggest possible email formats for the person based on:
   - Known company email patterns (first@company.com, first.last@company.com)
   - The person's name and role
3. The LLM response is a suggestion — we store it with `confidence` flag
4. This is **never** used for sending without human verification

### 6.2 Discovery Flow

```
reach.py discover <jid>
  │
  ├── 1. Load job from DB (company, title, team_name, url)
  │
  ├── 2. If LinkedIn job URL → extract recruiter (existing _RECRUITER_JS)
  │
  ├── 3. If company name exists → lookup company in DB
  │     ├── companies table (check if we already have data)
  │     └── If new → add to companies table
  │
  ├── 4. Find company LinkedIn page
  │     ├── Search LinkedIn for company page
  │     └── Get company slug/ID
  │
  ├── 5. Team contact discovery
  │     ├── If jobs.team_name set → search company people page
  │     └── Filter by team keywords → add contacts
  │
  ├── 6. My connections at company
  │     ├── LinkedIn search: 1st degree + currentCompany
  │     └── Fallback: company page connections section
  │
  ├── 7. Email enrichment (optional)
  │     └── LLM suggests email patterns for discovered contacts
  │
  └── 8. Emit structured output + NEXT command
```

### 6.3 Structured Output

```json
{
  "jid": "abc123def4567890",
  "company": "Acme Corp",
  "contacts": {
    "recruiters": [
      {"name": "Jane Smith", "role": "Talent Acquisition", "linkedin_url": "https://...", "source": "recruiter_auto"}
    ],
    "team_members": [
      {"name": "Bob Chen", "role": "ML Engineer @ AI/ML team", "linkedin_url": "https://...", "source": "team_search", "confidence": 0.85}
    ],
    "my_connections": [
      {"name": "Alice Wang", "role": "Senior Developer @ Acme Corp", "linkedin_url": "https://...", "source": "my_connection", "connection_degree": "1st"}
    ]
  },
  "email_candidates": [
    {"name": "Jane Smith", "suggested_emails": ["jane.smith@acme.com", "jane@acme.com"], "confidence": 0.7}
  ]
}
```

---

## 7. Component 4: Company Connections

### 7.1 My Network Discovery

The search for my connections at a target company uses:

**Primary: LinkedIn People Search**
```
URL: https://www.linkedin.com/search/results/people/
Params:
  - network: ["F"]           # 1st degree
  - currentCompany: [id]     # Company LinkedIn ID (not slug)
  - keywords: ""             # Optional search within
```
We need to:
1. Resolve company name → LinkedIn company ID (numerical)
2. This can be done by visiting company page and extracting from URL or page source
3. Company LinkedIn ID is embedded in page: `"companyId":12345` or `urn:li:company:12345`

**Fallback: Company Page Connections Section**
- On company page `https://www.linkedin.com/company/{slug}/`, there is often a "Connections" sidebar
- Or navigate to `https://www.linkedin.com/company/{slug}/people/`
- The page may show "X of your connections work here"
- Look for list items showing mutual connections

### 7.2 Connection Storage

Connections that are my 1st-degree contacts get:
- Stored in `company_connections` table (for cross-job reference)
- Also stored in `contacts` table linked to the specific job
- Distinguished by `source='my_connection'` and `connection_degree='1st'`

---

## 8. Component 5: Team Contact Lookup

### 8.1 Team Name Extraction

**From the LLM/orchestrator**:
During the enrich phase, after the LLM reads the job description, it can identify the team name. Two approaches:

**A. Auto-extraction in enrich.py**:
- After saving description text, run an LLM prompt to extract team/department
- Store in `jobs.team_name`
- Example prompt: `"From this job description, identify the specific team or department this role is for. Return just the team name or 'unknown'."`

**B. Manual via reach.py**:
- `reach.py discover <jid> --team "AI/ML"` — manually specify team
- LLM can also specify team when running discover

### 8.2 Team Member Search on LinkedIn

Once we have company name + team name:

1. Navigate to `https://www.linkedin.com/company/{slug}/people/`
2. This shows all employees with filters
3. Scroll to load more employees
4. Extract each visible employee card:
   - `a[href*="/in/"]` → profile URL + name
   - `.lt-line-clamp__line` or `.employee-card__name` → name
   - `.lt-line-clamp__line--last` or subtitle → role/title
   - `.employee-card__headline` → headline
5. Filter by team keywords:
   - If team_name is "AI/ML", look for roles containing "AI", "ML", "Machine Learning", "Data Science"
   - Keyword matching is case-insensitive
6. For matched employees, extract profile info and save as contacts

### 8.3 Profile Page Enrichment

For each discovered contact (optional, tier-2), we can navigate to their profile page to get:
- Email (if public — rare)
- More complete role description
- Mutual connections count
- Whether they have "Open to Work" / "Open to Hiring" badge

---

## 9. Orchestrator Integration

### 9.1 New Commands in Existing Pipeline

**extend `enrich.py`**:
- `enrich.py admit --category tech --team "AI/ML"` — add team name when admitting

**extend `report.py`**:
- `report.py contacts <jid>` — already exists, enhanced with new fields
- `report.py connections <company>` — show connections at a company
- `report.py outreach` — show pending outreach (not yet contacted)

### 9.2 Pipeline Integration Points

| Stage | What triggers | Action |
|-------|--------------|--------|
| `extracted` → `described` | enrich.py admit | Store team_name if provided |
| After `described` | Auto (configurable) | `reach.py discover` runs automatically |
| `tailored` | Auto | `reach.py email` / `reach.py message` for high-confidence contacts |
| `tailored` → `applied` | Manual | LLM reviews contacts, decides outreach strategy |

### 9.3 Orchestrator Signals

```
CONTACTS: 3 total (1 recruiter, 1 team, 1 connection)
CONTACT: 1 | Jane Smith | recruiter | Talent Acquisition | jane@acme.com | 0.9
CONTACT: 2 | Bob Chen | team | ML Engineer | linkedin | 0.7
CONTACT: 3 | Alice Wang | my_connection | Sr. Developer | linkedin | 1st degree
NEXT: reach.py email abc123def4567890 --contact 1    # Email recruiter
NEXT: reach.py message abc123def4567890 --contact 2   # DM team member
NEXT: reach.py message abc123def4567890 --contact 3   # Ask connection for referral
```

---

## 10. Selectors & DOM Investigation Methodology

### 10.1 Investigation Commands (to be run before implementing selectors)

For each LinkedIn page type we need to interact with, use the existing `act --inspect` equivalent:
- Open the page in Chrome via `enrich.py open <jid>` or manual navigation
- Use Playwright `page.evaluate()` or `page.content()` to dump the DOM
- Take screenshots for visual reference
- Identify stable selectors (aria labels, data-testid, role attributes)

### 10.2 Pages to Investigate

| Page | URL Pattern | What to find |
|------|------------|-------------|
| Company people | `/company/{slug}/people/` | Employee cards, pagination, filter bars |
| LinkedIn search | `/search/results/people/` | Search result cards, filter chips, network filter |
| Profile page | `/in/{username}/` | Message button, Connect button, contact info |
| Messaging compose | `/messaging/thread/new/` | Compose box, send button, recipient field |
| Connections at company | `/search/results/people/?network=F&currentCompany=` | Connection cards, degree badges |

### 10.3 Investigation Methodology

For each target page:

1. **Load the page** in Chrome via `enrich.py open` or direct Playwright goto
2. **Dump outer HTML**: `page.evaluate("() => document.documentElement.outerHTML")`
3. **Save to file**: Save HTML to `~/.ji/snapshots/investigate_{page_type}_{timestamp}.html`
4. **Analyze screenshot**: Save screenshot to `~/.ji/snapshots/investigate_{page_type}_{timestamp}.png`
5. **Identify selectors**: Look for:
   - `aria-label` attributes (most stable — LinkedIn uses them extensively)
   - `data-testid` attributes (LinkedIn uses these on newer pages)
   - `[data-control-name]` attributes (LinkedIn internal routing)
   - Class names (least stable — avoid hardcoding)
6. **Test selector**: Run `page.locator(selector).count()` and `page.locator(selector).first.is_visible()`
7. **Write JS probe**: Create a JS probe function (like `_RECRUITER_JS` in `linkedin.py`) that returns structured data from the page

### 10.4 Probe Functions to Develop

**`_COMPANY_PEOPLE_JS`** - Extract employees from company people page:
```javascript
() => {
  const employees = [];
  const cards = document.querySelectorAll('.org-people-card, .org-people-profile-card, [data-test-person-card], .reusable-search__result-container');
  for (const card of cards) {
    const link = card.querySelector('a[href*="/in/"]');
    if (!link) continue;
    const name = (link.textContent || '').trim();
    const href = link.href || '';
    const subtitle = card.querySelector('.artdeco-entity-lockup__subtitle, .lt-line-clamp__line');
    const role = subtitle ? (subtitle.textContent || '').trim() : '';
    const headline = card.querySelector('.artdeco-entity-lockup__caption, .lt-line-clamp__line--last');
    employees.push({
      name: name.replace(/\s+/g, ' '),
      role: role.replace(/\s+/g, ' '),
      headline: headline ? (headline.textContent || '').trim().replace(/\s+/g, ' ') : '',
      linkedin_url: href
    });
  }
  return employees;
}
```

**`_CONNECTIONS_SEARCH_JS`** - Extract connections from search results:
```javascript
() => {
  const connections = [];
  const cards = document.querySelectorAll('.reusable-search__result-container, .entity-result');
  for (const card of cards) {
    const link = card.querySelector('a[href*="/in/"]');
    if (!link) continue;
    const name = (link.textContent || '').trim();
    const href = link.href || '';
    const subtitle = card.querySelector('.entity-result__primary-subtitle, .lt-line-clamp__line');
    const role = subtitle ? (subtitle.textContent || '').trim() : '';
    const badge = card.querySelector('.entity-result__badge-text, .member-connection-badge');
    const degree = badge ? (badge.textContent || '').trim() : '';
    connections.push({
      name: name.replace(/\s+/g, ' '),
      role: role.replace(/\s+/g, ' '),
      linkedin_url: href,
      connection_degree: degree
    });
  }
  return connections;
}
```

**`_MESSAGE_SENT_JS`** - Verify message was sent:
```javascript
() => {
  const conversationList = document.querySelector('.msg-conversations-container__conversations-list');
  const newMessage = conversationList ? conversationList.querySelector('.msg-conversation-card--is-new') : null;
  const sendButton = document.querySelector('button.msg-form__send-button, button[aria-label="Send"]');
  const errorMsg = document.querySelector('.msg-form__error, [data-test-error]');
  return {
    inConversationList: !!conversationList,
    hasNewMessage: !!newMessage,
    sendButtonDisabled: sendButton ? sendButton.disabled : null,
    sendButtonVisible: sendButton ? sendButton.offsetParent !== null : false,
    hasError: !!errorMsg,
    errorText: errorMsg ? (errorMsg.textContent || '').trim() : ''
  };
}
```

### 10.5 Location of These Probes

All JS probes live:
- In `lib/linkedin_messaging.py` as module-level constants (like `_RECRUITER_JS` in `linkedin.py`)
- NOT in YAML — these are JS code, not configuration
- YAML is for widget config, form field patterns, etc.

---

## 11. Gmail Send Implementation Details

### 11.1 MIME Message Construction

```python
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import base64

def _build_mime(to, subject, body, cc=None, bcc=None):
    msg = MIMEMultipart('alternative')
    msg['To'] = to
    msg['Subject'] = subject
    msg['From'] = 'me'  # Gmail API replaces with authenticated user
    if cc:
        msg['Cc'] = cc
    
    # Plain text part
    part_text = MIMEText(body, 'plain', 'utf-8')
    msg.attach(part_text)
    
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {'raw': raw}
```

### 11.2 Send API Call

```python
def _cmd_email_send(to, subject, body_text, cc=None, bcc=None, email=None):
    service = _get_send_service(email)
    message = _build_mime(to, subject, body_text, cc, bcc)
    sent = service.users().messages().send(userId='me', body=message).execute()
    return sent['id']
```

### 11.3 Send-Scope Token Management

- Token file: `~/.config/gmail-cli/tokens/{email}.send.json`
- Auth command: `gmail-cli auth add <email> --services gmail.send`
- The `--services` flag is already accepted in the parser but was previously ignored — now it flows through:
  - `--services gmail` → uses `gmail.readonly` scope (existing)
  - `--services gmail.send` → uses `gmail.send` scope (new)
  - `--services gmail,gmail.send` → creates both tokens

---

## 12. Email Message Templates

### 12.1 Template Location

Email and message templates live in:
```
skills/job_intelligence/templates/
  ├── email_recruiter.md          # Email to recruiter about job
  ├── email_team_member.md        # Email to team member (informational interview)
  ├── email_connection.md         # Email to existing connection (referral request)
  ├── linkedin_recruiter.md       # LinkedIn DM to recruiter
  ├── linkedin_team_member.md     # LinkedIn DM to team member
  └── linkedin_connection.md      # LinkedIn DM to existing connection
```

### 12.2 Template Variables

Each template is a markdown file with `{variable}` placeholders:
- `{contact_name}` — Name of the person
- `{company}` — Company name
- `{job_title}` — Title of the position
- `{my_name}` — My name (from profile)
- `{connection_shared}` — Optional shared context (mutual connection, shared interest)

The LLM fills in templates with personalization. Templates are prompts, not static text — the LLM reads the template, adapts it to the person and context, then writes the final message.

### 12.3 Template Design Ethos

- All templates are stored as plain text in `templates/` directory
- They use `{variable}` syntax for LLM substitution
- The LLM reads the template AND the contact info AND the job info, then crafts a personalized message
- Templates are guidelines, not rigid formats
- No PII in templates — all personalization happens at send time

---

## 13. New Files Added

| File | Purpose |
|------|---------|
| `skills/job_intelligence/reach.py` | Main CLI for contact discovery and outreach |
| `skills/job_intelligence/lib/linkedin_messaging.py` | LinkedIn DM + connection request automation |
| `skills/job_intelligence/lib/contacts/__init__.py` | Contact discovery orchestration |
| `skills/job_intelligence/lib/contacts/discover.py` | Discovery logic (recruiter, team, connections) |
| `skills/job_intelligence/lib/contacts/enrich.py` | Email suggestion, profile enrichment |
| `skills/job_intelligence/templates/` | Message templates directory |
| `skills/job_intelligence/REACH_PHASE.md` | This document |
| `tests/test_reach.py` | Tests for reach module |
| `tests/test_linkedin_messaging.py` | Tests for LinkedIn messaging |

## 14. Existing Files Modified

| File | Changes |
|------|---------|
| `skills/gmail-cli/gmail_cli.py` | Add `gmail.send` scope, `email send` command, MIME builder |
| `skills/job_intelligence/enrich.py` | Accept `--team` flag on admit, store `jobs.team_name` |
| `skills/job_intelligence/linkedin.py` | Enhanced `_RECRUITER_JS`, store to contacts table |
| `skills/job_intelligence/lib/db/schema.py` | New columns on `contacts`, `jobs`, new tables |
| `skills/job_intelligence/lib/db/contacts.py` | Enhanced contact CRUD (source, confidence, etc.) |
| `skills/job_intelligence/report.py` | Enhanced contacts output, new outreach command |
| `skills/job_intelligence/lib/db/__init__.py` | Export new functions |
| `skills/job_intelligence/lib/config.py` | Add TEMPLATES_DIR path |
| `skills/job_intelligence/SKILL.md` | New commands, new stage |

---

## 15. Edge Cases & Failure Modes

### 15.1 Contact Discovery

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Company not on LinkedIn | No company page found | Store company as unknown, skip LinkedIn-based lookups |
| Team name too generic ("Engineering") | Too many matches | LLM reviews results, narrows keywords |
| No my-connections at company | Empty search results | Skip, emit `CONNECTIONS: 0` |
| LinkedIn rate-limited | HTTP 429, "Too many requests" | Backoff + retry (existing pattern in `tailor.py`) |
| LinkedIn not signed in | "Sign in" text on page | Emit `SIGN_IN_REQUIRED`, prompt for manual login |

### 15.2 Email Send

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Invalid recipient email | API returns error | Emit `INVALID_EMAIL`, flag for manual check |
| Send quota exceeded | HTTP 403 (daily limit) | Store in `contact_attempts` as failed, queue for later |
| Token expired | `invalid_grant` error | Prompt `gmail-cli auth add` re-auth |
| Missing send scope | API returns 403 (scope) | Prompt re-auth with `--services gmail.send` |
| Message too large | API error | Truncate body, retry |

### 15.3 LinkedIn DM

| Failure | Detection | Recovery |
|---------|-----------|----------|
| "Message" button hidden (not connected) | `connectBtn` visible, `msgBtn` hidden | Offer to send connection request instead |
| Premium required for message | Redirect to premium page | Emit `PREMIUM_REQUIRED`, flag contact |
| Message sending failed | Error text visible | Retry once, then flag for manual |
| Recipient not accepting messages | "Can't send message" text | Emit `MESSAGE_BLOCKED`, move on |
| LinkedIn messaging rate limit | Throttle response | Backoff (same pattern as tailor rate limits) |

### 15.4 One-Shot Guard

Following the existing one-shot submit pattern:
- `email_sent` flag on `contacts` table prevents re-sending
- `message_sent` flag on `contacts` table prevents re-DMing
- `--force` flag overrides (same as `apply.py act --submit --force`)
- `undo` command clears the flags (same as `tailor.py undo`)
- Unconfirmed DM sends are `uncertain`, not `failed` — attempt logged as
  `pending`, human checks inbox, then `update --set-sent` or `--force` retry

---

## 16. Testing Strategy

| Test | What it covers |
|------|---------------|
| `test_contact_discovery.py` | Contact discovery flow with mock company pages |
| `test_linkedin_messaging.py` | Message button detection, compose flow |
| `test_email_send.py` | MIME construction, send API interaction |
| `test_reach_pipeline.py` | Reach stage integration with existing pipeline |
| `test_company_connections.py` | Company connections search and fallback |
| `test_team_lookup.py` | Team name extraction, employee list filtering |
| Manual: inspect LinkedIn company pages | ✅ DONE (2026-07-30) — verified `li.org-people-profile-card__profile-card-spacing`, pagination buttons, slug verification (MongoDB = `mongodbinc`, ID 783611) |
| Manual: inspect LinkedIn messaging | ✅ DONE (2026-07-30) — verified typeahead flow; compose URL params DON'T resolve recipient; send button `button.msg-form__send-btn`; InMail composer for 2nd/3rd |
| Manual: inspect LinkedIn search results | ✅ DONE (2026-07-30) — URL structure verified working (filters appear); "No results found" is a valid zero-connections outcome |

---

## 17. Implementation Order

| Phase | What | Depends on |
|-------|------|-----------|
| **P1** | DB schema changes (new columns, new tables) | Nothing |
| **P2** | `gmail_cli.py` — add send scope + email send command | P1 |
| **P3** | `lib/linkedin_messaging.py` — DM + connect automation | P1 |
| **P4** | `lib/contacts/discover.py` — team lookup, connections search | P1, P3 |
| **P5** | `reach.py` — main CLI | P2, P3, P4 |
| **P6** | Pipeline integration (enrich --team, auto-discover) | P5 |
| **P7** | Templates directory + message templates | P5 |
| **P8** | Tests | ✅ DONE |
| **P9** | Live DOM investigation + selector verification | ✅ DONE (2026-07-30) — see §20 verified selectors |
| **P10** | Documentation (SKILL.md updates, this doc) | ✅ DONE |

---

## 18. Open Questions (To Resolve During Implementation)

1. ~~**Company LinkedIn ID resolution**: How to reliably map company name → LinkedIn company ID for the search URL?~~ **RESOLVED**: `_resolve_company_id` visits `/company/{slug}/` and extracts `urn:li:company:(\d+)`; persisted to `companies.linkedin_id` and reused across runs. `_find_company_slug` verifies the company name against the search card before returning.
   
2. **Email sending from LLM**: Should the LLM write the full email body or fill a template? **Decision**: LLM writes full body using template as guidance — more personalized.

3. **Multiple contacts per job**: How does the LLM choose which contact(s) to reach out to? **Decision**: The LLM reviews all discovered contacts and prioritizes by: (a) recruiter (most direct), (b) my connection (referral potential), (c) team member (informational).

4. ~~**LinkedIn session persistence**: The existing `chrome_manager.py` uses a persistent Chrome profile. LinkedIn login cookies persist. For DM, we need to verify the session is still valid before attempting.~~ **RESOLVED**: `_check_auth` verifies the session on every messaging/connect attempt before any action; verified live (2026-07-30) that the pipeline Chrome profile session persists.

5. ~~**Rate limiting coordination**: LinkedIn DM + company people scraping + connections search all hit LinkedIn's rate limits. Need a shared rate limiter or careful sequencing.~~ **PARTIAL**: `reach.py discover --all` processes jobs sequentially (one browser session, one at a time). Per-request throttling still not centralized — revisit if LinkedIn starts throttling.

---

## 19. Appendix: LinkedIn URL Reference

| Page | URL | Notes |
|------|-----|-------|
| Company people | `https://www.linkedin.com/company/{slug}/people/` | Shows employees; may need scrolling |
| Company about | `https://www.linkedin.com/company/{slug}/about/` | Has company size, industry |
| Search 1st deg at company | `https://www.linkedin.com/search/results/people/?network=%5B%22F%22%5D&currentCompany=%5B{id}%5D` | `id` is the numerical company ID |
| Search all at company | `https://www.linkedin.com/search/results/people/?currentCompany=%5B{id}%5D` | Shows all (may be limited) |
| Profile | `https://www.linkedin.com/in/{username}/` | Message button, contact info |
| Messaging | `https://www.linkedin.com/messaging/` | Message list |
| New message | `https://www.linkedin.com/messaging/thread/new/` | Compose — recipient via `recipient` param or `recipientUrn` |
| Job detail | `https://www.linkedin.com/jobs/view/{jobId}/` | Hiring team section |
| Inbox | `https://www.linkedin.com/inbox/` | Alternate messaging URL |

---

## 20. Appendix: Key Selectors — VERIFIED (2026-07-30 live DOM)

Verified against the real LinkedIn DOM (MongoDB company page, Jacob Anderson
profile, messaging compose) via Playwright probes. Selectors below are
confirmed working, not hypotheses.

```
Company People Page (/company/{slug}/people/):
  Employee card:      li.org-people-profile-card__profile-card-spacing
  Name link:          .artdeco-entity-lockup__title a[href*="/in/"]
                      (the IMAGE link comes first in DOM and has NO text —
                       always prefer the title link)
  Role:               .artdeco-entity-lockup__subtitle
  Degree:             .artdeco-entity-lockup__degree  ("· 2nd" → "2")
  Mutual connections: span.lt-line-clamp.t-12
  Pagination:         button[aria-label="Page N"]  (PAGINATED, NOT infinite scroll;
                      12 employees per page; window.scrollTo does nothing)
  URL cleanup:        strip ?miniProfileUrn=... query params from /in/ links
  Wrong slug:         "This LinkedIn Page isn't available" — slug verification
                      is essential (MongoDB's real slug is "mongodbinc")

Company ID:           "urn:li:company:783611" in company page innerHTML
                      (regex "urn:li:company:(\d+)")

Search Results (network=F&currentCompany=[id]):
  Result cards:       .reusable-search__result-container, .entity-result
  Name link:          a[href*="/in/"] span[aria-hidden="true"]
  Subtitle:           .entity-result__primary-subtitle
  Degree badge:       .entity-result__badge-text, .member-connection-badge
  NOTE: URL structure VERIFIED working (filters appear as active chips);
        "No results found" is a valid outcome when there are zero
        1st-degree connections at the company → fallback engages.

Profile Page (new hashed-class layout — NO aria-label on actions):
  Message link:       a[href*="/messaging/compose/"]  (text "Message")
  Connect link:       a[href*="/preload/custom-invite/"]  (text "Connect")
  Follow button:      button[aria-label="Follow <name>"]
  Premium nav link:   ALWAYS present on free accounts — never use global
                      a[href*="premium"] for premium detection

Messaging DM — VERIFIED WORKING FLOW (compose URL params DON'T work):
  Compose page:       /messaging/thread/new/
  Recipient typeahead: input.msg-connections-typeahead__search-field
                      ("Type a name or multiple names")
  Typing:             REAL keyboard events required (page.keyboard.type);
                      .fill() does NOT trigger suggestions
  Suggestion item:    li.msg-connections-typeahead__search-result
                      (first is auto-highlighted aria-selected="true")
  Suggestion click:   REAL mouse events required (JS .click() and Enter do
                      NOT register — Ember delegated handlers)
  Message box:        div.msg-form__contenteditable (aria-label "Write a message…")
  Send button:        button.msg-form__send-btn[type="submit"]
                      (NO aria-label; disabled until text; enables after typing)
  InMail composer:    .msg-inmail-credits-display banner + input[name="subject"]
                      (2nd/3rd-degree contacts — premium signal)
  Free composer:      1st-degree contacts — no subject line, no banner
  Post-send signals:  .msg-s-message-list, .msg-conversation-card--is-new,
                      send button gone/disabled, .msg-form__error

Connect — VERIFIED WORKING FLOW (direct URL, no click needed):
  Invite URL:         /preload/custom-invite/?vanityName={username}
                      (renders the send-invite modal AS A PAGE — verified)
  Modal:              .artdeco-modal.send-invite
  "Add a note":       button[aria-label="Add a note"]
  Note textarea:      textarea[name="message"]
                      (placeholder "Ex: We know each other from…";
                       appears only AFTER Add-a-note click — verified)
  Send (with note):   button[aria-label="Send invitation"]
                      (disabled until note typed — verified enable)
  Send (no note):     button[aria-label="Send without a note"]
  Cancel:             button[aria-label="Cancel adding a note"]
  Outcome detection:  .artdeco-inline-feedback--error / [data-test-error]
                      absence + modal gone → sent; else uncertain.
  NOTE: the send CLICK itself is the one unverified step (no live send
        was performed); the one-shot guard + uncertain status covers it.
        When a real request is sent: profile Connect link switches to a
        Pending state (componentkey ConnectButtonstate:invitation:pending).
```
