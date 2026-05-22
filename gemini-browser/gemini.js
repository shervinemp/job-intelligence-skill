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

  await modeBtn().click();
  await wait(2000);

  // Check for rate limit inside the picker (disabled mode option with "Limit resets" text)
  const limitInPicker = await page.evaluate(() => {
    const items = document.querySelectorAll('[data-test-id^="bard-mode-option-"]');
    for (const item of items) {
      const text = (item.textContent || '').trim();
      if (/limit resets/i.test(text)) {
        const idx = text.indexOf('Limit resets ');
        return { timedOut: true, resetsAt: idx >= 0 ? text.substring(idx + 13).trim() : 'unknown' };
      }
    }
    return { timedOut: false };
  });
  if (limitInPicker.timedOut) {
    await page.keyboard.press('Escape'); await wait(500);
    return { status: 'timedOut', resetsAt: limitInPicker.resetsAt };
  }

  // 1. Select 3.5 Flash if not already selected
  const flashSelected = await page.evaluate(() => {
    const items = document.querySelectorAll('[data-test-id^="bard-mode-option-"]');
    for (const item of items) {
      if ((item.textContent || '').includes('Flash') && !(item.textContent || '').includes('Lite')) {
        const isSel = item.classList.contains('selected');
        if (!isSel) item.click();
        return isSel;
      }
    }
    return false;
  });
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

    await openGem(page, opts.gem);

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
        await openGem(page, opts.gem);
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

    console.log(resp);

    if (opts.outputFile) {
      try { fs.writeFileSync(opts.outputFile, resp, 'utf8'); } catch (e) { log(`write failed: ${e.message}`); }
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
    await page.close();
    process.exit(0);
  } catch (e) {
    console.error(e.message);
    try { await page.close(); } catch (e) { }
    process.exit(1);
  }
})();
