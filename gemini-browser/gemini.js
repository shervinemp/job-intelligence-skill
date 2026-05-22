/**
 * Gemini Browser Automation
 *
 * Connects to Chrome via CDP, navigates to the Application Optimizer
 * gem, sets 3.5 Flash + Extended thinking, sends a prompt, reads response.
 *
 * Start Chrome:
 *   See ~/.openclaw/chrome-config.json for current paths (managed by chrome_manager.py)
 *
 * Usage:
 *   node gemini.js "your prompt"
 *   node gemini.js --gem-url https://...    Activate custom gem by URL
 *   node gemini.js --state                  Show state
 *   node gemini.js --gems                   List sidebar gems
 *   node gemini.js --dump                   Dump page structure
 *   node gemini.js --login                  Save session
 *   node gemini.js --output FILE            Save response to FILE
 *   node gemini.js --app-dir DIR            Save response to DIR/gemini_response.txt
 *   node gemini.js --prompt-file FILE       Read prompt from FILE
 */

const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');
const os = require('os');

const SESSION = path.join(__dirname, 'session.json');

// Load config from chrome_manager (Python module writes this on import)
let CHROME_PATH, CHROME_PROFILE, CDP;
const configPath = path.join(os.homedir(), '.openclaw', 'chrome-config.json');
try {
  const cfg = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  CHROME_PATH = cfg.CHROME_PATH;
  CHROME_PROFILE = cfg.CHROME_PROFILE;
  CDP = cfg.CDP_URL;
} catch (e) {
  CHROME_PATH = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  CHROME_PROFILE = path.join(os.homedir(), '.openclaw', 'chrome-profile');
  CDP = 'http://127.0.0.1:9222';
}

function loadGemId() {
  const envPath = path.join(__dirname, '..', 'job_intelligence', '.env');
  try {
    const lines = fs.readFileSync(envPath, 'utf8').split('\n');
    for (const line of lines) {
      const m = line.match(/^GEM_ID\s*=\s*(.+)$/);
      if (m) return m[1].trim();
    }
  } catch (e) { }
  return '4203d06f5d81';
}
const GEM_ID = loadGemId();
const GEM = `https://gemini.google.com/gem/${GEM_ID}`;

function log(m) { console.error(`[gemini] ${m}`); }
function die(m) { console.error(m); process.exit(1); }
function wait(ms) { return new Promise(r => setTimeout(r, ms)); }

// ─── Args ────────────────────────────────────────────────

function args() {
  const a = process.argv.slice(2);
  let prompt = null, gem = null, action = 'prompt', outputFile = null, appDir = null, promptFile = null;
  for (let i = 0; i < a.length; i++) {
    const v = a[i];
    if (v === '--help') {
      console.log(
        'Usage:\n  node gemini.js "prompt"            Send prompt\n' +
        '  node gemini.js --gem-url URL      Use custom gem URL\n' +
        '  node gemini.js --state            Show state\n' +
        '  node gemini.js --gems             List gems\n' +
        '  node gemini.js --dump             Dump page\n' +
        '  node gemini.js --login            Save session\n' +
        '  node gemini.js --output FILE      Save response to FILE\n' +
        '  node gemini.js --app-dir DIR      Save response to DIR/gemini_response.txt\n' +
        '  node gemini.js --prompt-file FILE  Read prompt from FILE\n'
      );
      process.exit(0);
    }
    else if (v === '--state') action = 'state';
    else if (v === '--gems') action = 'gems';
    else if (v === '--dump') action = 'dump';
    else if (v === '--login') action = 'login';
    else if (v === '--gem-url' && i + 1 < a.length) gem = a[++i];
    else if (v === '--output' && i + 1 < a.length) outputFile = a[++i];
    else if (v === '--app-dir' && i + 1 < a.length) appDir = a[++i];
    else if (v === '--prompt-file' && i + 1 < a.length) promptFile = a[++i];
    else if (!prompt) prompt = v;
  }
  return { prompt, gem, action, outputFile, appDir, promptFile };
}

// ─── Connect ─────────────────────────────────────────────

async function connect() {
  let b;
  try {
    b = await chromium.connectOverCDP(CDP);
  } catch (e) {
    log('Chrome not responding, starting it...');
    const { spawn } = require('child_process');
    try {
      spawn(CHROME_PATH, ['--user-data-dir=' + CHROME_PROFILE, '--remote-debugging-port=9222', '--no-first-run', '--no-default-browser-check'], { detached: true, stdio: 'ignore' });
      await wait(6000);
      b = await chromium.connectOverCDP(CDP);
    } catch (e2) {
      die('Could not connect to Chrome. Start manually with remote debugging on port 9222');
    }
  }
  const ctx = b.contexts()[0];
  if (!ctx) die('No browser context');
  const p = await ctx.newPage();
  return { browser: b, page: p };
}

async function saveSession(page) {
  try {
    const c = await page.context().cookies();
    const auth = c.filter(x =>
      x.name.includes('SID') || x.name.includes('HSID') || x.name.includes('SSID') ||
      x.name.includes('__Secure-1P') || x.name.includes('__Secure-3P') ||
      x.name.includes('__Host') || x.name === 'AEC' || x.name === 'NID'
    );
    if (auth.length) fs.writeFileSync(SESSION, JSON.stringify({ cookies: auth, origins: [] }, null, 2));
  } catch (e) { }
}

// ─── Mode: 3.5 Flash + Extended thinking ────────────────

async function ensureMode(page) {
  const modeBtn = () => page.locator('[data-test-id="bard-mode-menu-button"]').first();

  let count;
  try { count = await modeBtn().count({ timeout: 2000 }); } catch (e) { count = 0; }
  if (count === 0) return { status: 'failed', reason: 'mode button not found' };

  // Check for rate limit in page body BEFORE opening picker
  const bodyLimit = await page.evaluate(() => {
    const body = document.body.innerText;
    const patterns = [
      /usage.*(?:limit|cap).*reached/i, /rate limit/i, /too many requests/i,
      /try again later/i, /maximum number.*(?:message|request)/i, /limit.*reset/i,
      /you.*ran.*out/i, /out.*of.*requests/i, /quota.*exceeded/i,
      /upgrade to.*pro/i, /pro.*(?:usage|limit).*reset/i
    ];
    for (const p of patterns) {
      const m = body.match(p);
      if (m) {
        const resetMatch = body.match(/(?:resets?\s+at?\s+)?(.+?(?:\d{1,2}:\d{2}|[ap]m|minutes|hours))/i);
        return { timedOut: true, resetsAt: resetMatch ? resetMatch[1].trim() : 'unknown' };
      }
    }
    return { timedOut: false };
  });
  if (bodyLimit.timedOut) {
    await page.keyboard.press('Escape'); await wait(500);
    return { status: 'timedOut', resetsAt: bodyLimit.resetsAt };
  }

  await modeBtn().click();
  await wait(2000);

  // Pass 1: click Flash to trigger real state (cached values may be stale)
  await page.evaluate(() => {
    const items = document.querySelectorAll('[data-test-id^="bard-mode-option-"]');
    for (const item of items) {
      const text = (item.textContent || '').trim();
      if (text.includes('Flash') && !text.includes('Lite')) {
        if (/limit resets/i.test(text)) {
          // Stale limit shown — still click to trigger server update
        }
        if (!item.classList.contains('selected')) item.click();
        return;
      }
    }
  });
  await wait(1000);
  await page.keyboard.press('Escape');
  await wait(1000);

  // Pass 2: re-open to read real state
  await modeBtn().click();
  await wait(2000);

  const flashResult = await page.evaluate(() => {
    const items = document.querySelectorAll('[data-test-id^="bard-mode-option-"]');
    for (const item of items) {
      const text = (item.textContent || '').trim();
      if (text.includes('Flash') && !text.includes('Lite')) {
        if (/limit resets/i.test(text)) {
          const idx = text.indexOf('Limit resets ');
          return { status: 'timedOut', resetsAt: idx >= 0 ? text.substring(idx + 13).trim() : 'unknown' };
        }
        if (!item.classList.contains('selected')) item.click();
        return { status: 'ok' };
      }
    }
    return { status: 'not found' };
  });
  if (flashResult.status === 'timedOut') {
    await page.keyboard.press('Escape'); await wait(500);
    return { status: 'timedOut', resetsAt: flashResult.resetsAt };
  }
  await wait(1500);

  // 2. Expand thinking level and set to Extended
  await page.evaluate(() => {
    const items = document.querySelectorAll('gem-menu-item');
    for (const item of items) {
      if ((item.textContent || '').includes('Thinking level') && !(item.textContent || '').includes('Extended')) {
        item.click();
        return;
      }
    }
  });
  await wait(1500);

  await page.evaluate(() => {
    const items = document.querySelectorAll('gem-menu-item');
    for (const item of items) {
      if ((item.textContent || '').includes('Extended')) {
        item.click();
        return;
      }
    }
  });
  await wait(1000);

  await page.keyboard.press('Escape');
  await wait(500);
  return { status: 'ok' };
}

async function checkMode(page) {
  const modeBtn = () => page.locator('[data-test-id="bard-mode-menu-button"]').first();
  let count;
  try { count = await modeBtn().count({ timeout: 2000 }); } catch (e) { count = 0; }
  if (count === 0) return { activeTier: 'Unknown', thinkingLevel: 'Unknown' };

  const text = await modeBtn().textContent();
  const isExtended = await page.evaluate(() => {
    const btn = document.querySelector('[data-test-id="bard-mode-menu-button"]');
    if (!btn) return false;
    return (btn.textContent || '').includes('Extended');
  });

  let activeTier = 'Flash';
  if (text.includes('Pro')) activeTier = 'Pro';
  else if (text.includes('Lite')) activeTier = 'Flash-Lite';
  else if (text.includes('Flash')) activeTier = 'Flash';

  return { activeTier, thinkingLevel: isExtended ? 'Extended' : 'Standard' };
}

// ─── Gem Navigation ──────────────────────────────────────

const _LIMIT_PATTERNS = [
  /usage.*(?:limit|cap).*reached/i, /rate limit/i, /too many requests/i,
  /try again later/i, /maximum number.*(?:message|request)/i, /limit.*reset/i,
  /you.*ran.*out/i, /out.*of.*requests/i, /quota.*exceeded/i,
  /upgrade to.*pro/i, /pro.*(?:usage|limit).*reset/i,
  /limit.*reached/i, /gemini.*(?:can't|unable|not available)/i
];

function _checkLimitInText(text) {
  for (const p of _LIMIT_PATTERNS) {
    const m = text.match(p);
    if (m) {
      const resetMatch = text.match(/(?:resets?\s+at?\s+)?(.+?(?:\d{1,2}:\d{2}|[ap]m|minutes|hours))/i);
      return { timedOut: true, resetsAt: resetMatch ? resetMatch[1].trim() : 'unknown' };
    }
  }
  return { timedOut: false };
}

async function openGem(page, gemUrl) {
  const url = gemUrl || GEM;
  log(`Navigating to: ${url}`);
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });

  if (!page.url().includes('/gem/')) {
    const body = await page.evaluate(() => document.body.innerText.substring(0, 200));
    die(`Gem not found at ${url}. Response: ${body.substring(0, 100)}`);
  }

  // Check for rate limit immediately after page load
  const bodyText = await page.evaluate(() => document.body.innerText);
  const limit = _checkLimitInText(bodyText);
  if (limit.timedOut) return limit;

  for (let i = 0; i < 4; i++) {
    const hasInput = await page.evaluate(() => !!document.querySelector('[contenteditable="true"]'));
    if (hasInput) break;
    // Check again each iteration in case a blocking overlay appeared
    const t = await page.evaluate(() => document.body.innerText);
    const l = _checkLimitInText(t);
    if (l.timedOut) return l;
    await wait(2000);
  }
  for (let i = 0; i < 4; i++) {
    const hasConvs = await page.evaluate(() => document.querySelectorAll('[data-test-id="conversation"]').length > 0);
    if (hasConvs) break;
    await wait(2000);
  }
  await wait(1000);
  try { await page.bringToFront(); } catch (e) { }
  return { timedOut: false };
}

// ─── Prompt ──────────────────────────────────────────────

async function send(page, text) {
  await wait(1000);
  const el = await page.$('[contenteditable="true"]');
  if (!el) die('No chat input');

  const box = await el.boundingBox();
  if (box) await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await wait(500);

  await page.evaluate(t => {
    const e = document.querySelector('[contenteditable="true"]');
    if (e) { e.textContent = t; e.dispatchEvent(new Event('input', { bubbles: true })); }
  }, text);
  await wait(1000);

  let sendBtn = await page.$('button[aria-label="Send message"]');
  if (!sendBtn) sendBtn = await page.$('[data-test-id="send-button-container"] button');
  if (sendBtn) {
    await sendBtn.click();
  } else {
    await page.keyboard.press('Enter');
  }
  await wait(1500);
  log('Sent!');
}

async function read(page, timeout = 360000) {
  const start = Date.now();
  const before = await page.evaluate(() => document.body.innerText);
  log('Waiting...');

  let text = before;
  while (Date.now() - start < timeout) {
    await wait(3000);
    text = await page.evaluate(() => document.body.innerText);
    if (text !== before) break;
  }
  if (text === before) return '(timeout - no change)';

  while (Date.now() - start < timeout) {
    await wait(1000);
    const done = await page.evaluate(() => {
      const stopBtn = document.querySelector('button[aria-label="Stop response"]');
      const copyBtn = document.querySelector('copy-button');
      return { stop: !!stopBtn, copy: !!copyBtn };
    });
    if (done.copy && !done.stop) break;
  }
  log(`gen done ${Math.round((Date.now() - start) / 1000)}s`);

  const resp = await page.evaluate(() => {
    let el = document.querySelector('message-content .markdown');
    if (!el) el = document.querySelector('.markdown.markdown-main-panel');
    if (!el) el = document.querySelector('structured-content-container .container');
    if (el) {
      let t = (el.innerText || el.textContent || '').trim();
      if (t.length > 10) return t.substring(0, 50000);
    }

    const lines = (document.body.innerText || '').split('\n').filter(l => l.trim());
    const skip = ['New chat', 'My stuff', 'Notebooks', 'Gems', 'Chats', 'Debugger',
                  'Application Optimizer', 'Custom Gem', 'Show thinking', 'said',
                  'Git Projects'];
    let text = lines.filter((l, i) => {
      if (i === 0) return false;
      return !skip.some(s => l.trim().startsWith(s) || l.trim() === s);
    }).join('\n').trim();
    if (text.length < 20) text = lines.slice(1).join('\n').trim();
    const blocks = document.querySelectorAll('pre code');
    for (const cb of blocks) {
      const code = (cb.textContent || '').trim();
      if (code.length > 50) text += '\n```python\n' + code + '\n```\n\n';
    }
    return text.substring(0, 50000);
  });
  if (resp && resp.length > 10) {
    log(`${Math.round((Date.now() - start) / 1000)}s (${resp.length}b)`);
    return resp;
  }
  return '(empty)';
}

// ─── Delete last conversation ──────────────────────────

async function deleteChat(page, convTitle) {
  try {
    await wait(2000);

    const onGem = await page.evaluate(() => location.href.includes('/gem/'));
    if (!onGem) { log('deleteChat: not on gem page'); return; }

    if (convTitle) {
      const match = await page.evaluate((title) => {
        const conv = document.querySelector('[data-test-id="conversation"]');
        if (!conv) return false;
        return (conv.textContent || '').trim().substring(0, 40) === title;
      }, convTitle);
      if (!match) { log('deleteChat: conversation mismatch, skipping'); return; }
    }

    const moreBtn = await page.evaluate(() => {
      const first = document.querySelector('[data-test-id="conversation"]');
      if (!first) return false;
      first.scrollIntoView({ block: 'center' });
      const btn = document.querySelector('button[data-test-id="actions-menu-button"]');
      if (btn) { btn.click(); return true; }
      return false;
    });
    if (!moreBtn) { log('deleteChat: no actions button'); return; }
    await wait(1500);

    const deleteBtn = await page.$('[data-test-id="delete-button"]');
    if (!deleteBtn) { await page.keyboard.press('Escape'); return; }
    await deleteBtn.click();
    await wait(1000);

    const confirmBtn = await page.$('[data-test-id="confirm-button"]');
    if (confirmBtn) await confirmBtn.click();
    await wait(1500);
    log('deleteChat: done');
  } catch (e) { log(`deleteChat error: ${e.message}`); }
}

// ─── Dump ────────────────────────────────────────────────

async function dump(page) {
  const d = await page.evaluate(() => {
    return {
      url: location.href, title: document.title,
      buttons: Array.from(document.querySelectorAll('button')).filter(x => x.textContent || x.getAttribute('aria-label')).map(x => ({ t: (x.textContent || '').trim().substring(0, 50), a: (x.getAttribute('aria-label') || '').substring(0, 50) })),
      body: document.body.innerText.substring(0, 1000),
    };
  });
  console.log(`\n${d.url}\n${d.title}\n`);
  d.buttons.forEach(b => console.log(`  "${b.t}" ${b.a ? 'aria="' + b.a + '"' : ''}`));
  console.log('\n' + d.body);
}

// ─── Main ────────────────────────────────────────────────

(async () => {
  const opts = args();

  if (opts.promptFile) {
    try { opts.prompt = fs.readFileSync(opts.promptFile, 'utf8').trim(); } catch (e) { die(`Cannot read prompt file: ${e.message}`); }
  }
  if (opts.action === 'prompt' && !opts.prompt) die('Usage: node gemini.js "your prompt"');

  const { browser, page } = await connect();
  try { await page.bringToFront(); } catch (e) { }

  try {
    if (opts.action === 'dump') {
      await page.goto(GEM, { waitUntil: 'domcontentloaded', timeout: 15000 });
      await wait(5000);
      await dump(page); browser.close(); return;
    }

    if (opts.action === 'state') {
      if (!page.url().includes('/gem/')) {
        await page.goto(GEM, { waitUntil: 'domcontentloaded', timeout: 15000 });
        await wait(5000);
      }
      const mode = await checkMode(page);
      console.log(`URL: ${page.url()}\nTitle: ${await page.title()}\nMode: ${mode.activeTier} | Thinking: ${mode.thinkingLevel}`);
      await saveSession(page); browser.close(); return;
    }

    if (opts.action === 'login') {
      const ok = await page.evaluate(() => (document.body.innerText.includes('New chat') || document.body.innerText.includes('Conversation with')) && !document.body.innerText.includes('Sign in'));
      log(ok ? 'Valid.' : 'Not signed in.');
      if (ok) await saveSession(page);
      browser.close(); return;
    }

    if (opts.action === 'gems') {
      const mode = await checkMode(page);
      const gems = await page.evaluate(() => {
        const c = document.querySelector('.gems-list-container');
        return c ? Array.from(c.querySelectorAll('button, a, [role="button"]')).map(x => (x.innerText || x.textContent || '').trim()).filter(x => x && x !== 'Gems') : [];
      });
      console.log(`${mode.activeTier} | Thinking: ${mode.thinkingLevel}\n`);
      if (gems.length) gems.forEach(g => console.log(`  - ${g}`));
      await saveSession(page); browser.close(); return;
    }

    if (!opts.prompt) die('Usage: node gemini.js "your prompt"');

    let og = await openGem(page, opts.gem);
    if (og.timedOut) {
      const out = JSON.stringify({ error: 'RATE_LIMIT', resetsAt: og.resetsAt || 'unknown' });
      console.error(out); process.exit(2);
    }

    const modeSet = await ensureMode(page);
    if (modeSet.status === 'timedOut') {
      const out = JSON.stringify({ error: 'RATE_LIMIT', resetsAt: modeSet.resetsAt || 'unknown' });
      console.error(out);
      process.exit(2);
    }
    if (modeSet.status !== 'ok') {
      die('Could not set Flash + Extended mode.');
    }
    log('3.5 Flash + Extended thinking');

    let resp = null;
    for (let attempt = 0; attempt < 2; attempt++) {
      if (attempt > 0) {
        log(`Retry ${attempt + 1}...`);
        og = await openGem(page, opts.gem);
        if (og.timedOut) {
          const out = JSON.stringify({ error: 'RATE_LIMIT', resetsAt: og.resetsAt || 'unknown' });
          console.error(out); process.exit(2);
        }
        const retrySet = await ensureMode(page);
        if (retrySet.status === 'timedOut') {
          const out = JSON.stringify({ error: 'RATE_LIMIT', resetsAt: retrySet.resetsAt || 'unknown' });
          console.error(out); process.exit(2);
        }
        if (retrySet.status !== 'ok') { die('Could not set mode on retry.'); }
      }
      await send(page, opts.prompt);
      resp = await read(page);
      if (resp && resp !== '(timeout)' && resp !== '(timeout - no change)' && resp.length >= 20) break;
    }

    if (!resp || resp === '(timeout)' || resp === '(timeout - no change)' || resp.length < 20) {
      console.error(`Gemini error: ${resp || 'empty response'}`);
      process.exit(1);
    }

    if (opts.outputFile) {
      try { fs.writeFileSync(opts.outputFile, resp, 'utf8'); } catch (e) { log(`write failed: ${e.message}`); }
    } else {
      console.log(resp);
    }
    if (opts.appDir) {
      try {
        fs.mkdirSync(opts.appDir, { recursive: true });
        fs.writeFileSync(path.join(opts.appDir, 'gemini_response.txt'), resp, 'utf8');
      } catch (e) { log(`app-dir write failed: ${e.message}`); }
    }

    const convTitle = await page.evaluate(() => {
      const el = document.querySelector('[data-test-id="conversation"]');
      return el ? (el.textContent || '').trim().substring(0, 40) : null;
    });

    await deleteChat(page, convTitle);
    process.exit(0);
  } catch (e) {
    console.error(e.message);
    try { await page.close(); } catch (e) { }
    process.exit(1);
  }
})();
