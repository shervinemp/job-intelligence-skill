"""mock_page.py — CorpusPage: replay real probe JS against saved HTML.

A test harness that loads a corpus snapshot (real DOM HTML captured
from a live platform) and executes the real `_SCAN_JS` (capability
scan) and `_READER_JS` (field reader) against it using node + jsdom.

This lets us test the probe cascade end-to-end WITHOUT a browser, a
Playwright install, or touching real job applications. A filler or
capability-scanner change that breaks Workday dropdown detection
fails the test BEFORE the next real run.

Usage in tests:

    page = CorpusPage.from_hash(profile_hash)
    profile = page.scan_capabilities()
    fields = page.read_fields(scope="dialog", custom_widgets={...})

    # Or load from a raw HTML fixture:
    page = CorpusPage.from_html(html_string, url="https://...")

CorpusPage implements the minimum Playwright Page surface that the
probe cascade touches: `evaluate()`, `url`, `frames`, `screenshot()`,
`locator()`, `expect_file_chooser()`. It delegates the JS execution
to node+jsdom via subprocess.

Requires: node and jsdom installed. If jsdom is missing, tests that
need it skip with a clear message.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

# Cache the node jsdom bridge script path
_BRIDGE_SCRIPT = None


# The bridge JS written to a temp file. It constructs a JSDOM window
# from the input HTML, then evaluates the user JS *inside* that
# window's global scope by injecting a <script> tag. This is the only
# way jsdom lets us run code with `document`, `window`, etc. in scope.
_BRIDGE_JS = r"""
const fs = require('fs');
const path = require('path');

// Locate jsdom
let jsdomPath = null;
for (const candidate of [
    process.env.JSDOM_NODE_MODULES,
    path.join(process.env.HOME || process.env.USERPROFILE || '', '.openclaw/workspace/tmp/opencode/node_modules'),
    path.join(__dirname, '..', 'node_modules'),
]) {
    if (candidate && fs.existsSync(path.join(candidate, 'jsdom', 'package.json'))) {
        jsdomPath = candidate;
        break;
    }
}
if (!jsdomPath) { console.error('JSDOM_NOT_FOUND'); process.exit(2); }

const { JSDOM } = require(path.join(jsdomPath, 'jsdom'));

let input = '';
process.stdin.on('data', d => input += d);
process.stdin.on('end', () => {
    try {
        const { html, js, args, url } = JSON.parse(input);
        const dom = new JSDOM(html, {
            url: url || 'https://corpus.test/',
            runScripts: 'dangerously',
            pretendToBeVisual: true,
        });
        const { window } = dom;
        // Patch: jsdom doesn't implement offsetParent by default. For
        // probe JS, we treat elements with style.display !== 'none' as
        // visible (offsetParent !== null). Good enough for offline tests.
        Object.defineProperty(window.HTMLElement.prototype, 'offsetParent', {
            get() {
                if (this.style && this.style.display === 'none') return null;
                if (this.hidden) return null;
                return this.parentNode || null;
            },
        });
        // Patch: jsdom doesn't implement innerText. Alias to textContent.
        Object.defineProperty(window.HTMLElement.prototype, 'innerText', {
            get() { return this.textContent || ''; },
            set(v) { this.textContent = String(v || ''); },
        });
        // Patch: getBoundingClientRect — jsdom returns zeros, which is fine.
        // Patch: shadowRoot needs attachShadow; jsdom supports it but
        // only if 'secretCanvas' is used. We skip shadow-DOM tests.

        // Inject the user JS as a <script> tag inside the window so
        // `document`, `window`, and other globals are in scope. We
        // capture the result via a global variable.
        const resultVar = '__corpus_result_' + Date.now();
        const wrappedJs = `
            try {
                const fn = ${js};
                window.${resultVar} = (typeof fn === 'function') ? fn(${JSON.stringify(args)}) : fn;
            } catch (e) {
                window.${resultVar} = { __error: e.message };
            }
        `;
        const scriptEl = window.document.createElement('script');
        scriptEl.textContent = wrappedJs;
        window.document.body.appendChild(scriptEl);
        // Give microtasks a tick to settle (Promise-based JS like
        // MutationObserver waits). 0ms timeout is enough.
        setTimeout(() => {
            const result = window[resultVar];
            if (result && result.__error) {
                console.error('JS_RUN_FAIL: ' + result.__error);
                process.exit(4);
            }
            // Strip DOM nodes + circular refs by serializing with a
            // replacer. Guard against undefined (JSON.stringify(undefined)
            // returns undefined → stdout.write throws).
            let serialized;
            try {
                serialized = JSON.stringify(result, (key, value) => {
                    if (value && value.nodeType !== undefined) return null;
                    if (typeof value === 'function') return null;
                    return value;
                });
            } catch (e) {
                serialized = JSON.stringify({ __serialize_error: e.message });
            }
            if (serialized === undefined) serialized = 'null';
            process.stdout.write(serialized);
            process.exit(0);
        }, 10);
    } catch (e) {
        console.error('BRIDGE_FAIL: ' + e.message);
        process.exit(1);
    }
});
"""


def _find_jsdom():
    """Locate a jsdom install. Returns the node_modules path or None."""
    candidates = [
        os.environ.get("JSDOM_NODE_MODULES"),
        # Workspace tmp (where we installed it for tests)
        str(Path(os.path.expanduser("~")) / ".openclaw" / "workspace" / "tmp" / "opencode" / "node_modules"),
        # Local node_modules in skill dir
        str(Path(__file__).resolve().parent.parent.parent / "node_modules"),
    ]
    for p in candidates:
        if p and (Path(p) / "jsdom" / "package.json").exists():
            return p
    return None


def _bridge_script():
    """Write (once) and return the path to the jsdom bridge JS."""
    global _BRIDGE_SCRIPT
    if _BRIDGE_SCRIPT and os.path.exists(_BRIDGE_SCRIPT):
        return _BRIDGE_SCRIPT
    fd, p = tempfile.mkstemp(suffix="_bridge.js", prefix="corpus_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(_BRIDGE_JS)
    _BRIDGE_SCRIPT = p
    return p


class CorpusPage:
    """A test page that runs real probe JS against saved HTML via jsdom."""

    def __init__(self, html: str, url: str = "https://corpus.test/"):
        self._html = html
        self.url = url
        self.title = lambda: "Corpus Page"
        self.frames = []  # jsdom doesn't simulate iframes
        self._screenshot_paths = []

    @classmethod
    def from_hash(cls, profile_hash: str) -> "CorpusPage":
        """Load a corpus snapshot by profile hash."""
        from apply.common.corpus import load_html, load_sidecar
        html = load_html(profile_hash)
        if html is None:
            raise FileNotFoundError(f"no corpus snapshot for hash {profile_hash}")
        sidecar = load_sidecar(profile_hash) or {}
        return cls(html, url=sidecar.get("url", "https://corpus.test/"))

    @classmethod
    def from_html(cls, html: str, url: str = "https://corpus.test/") -> "CorpusPage":
        """Build from a raw HTML string (for synthetic test fixtures)."""
        return cls(html, url=url)

    def evaluate(self, js, *args, **kwargs):
        """Run JS in the jsdom window. Returns the JSON-serializable result.

        `js` is a string of JS code (typically an arrow function) —
        evaluated inside the JSDOM window's global scope via injected
        <script> tag. Mirrors Playwright's page.evaluate API.
        """
        bridge = _bridge_script()
        arg_value = args[0] if args else (kwargs.get("arg") if "arg" in kwargs else None)
        payload = {
            "html": self._html,
            "js": js,
            "args": arg_value,
            "url": self.url,
        }
        proc = subprocess.run(
            ["node", bridge],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 2:
            raise ImportError("jsdom not installed — npm install jsdom")
        if proc.returncode != 0:
            raise RuntimeError(f"jsdom bridge failed (rc={proc.returncode}): {proc.stderr[:500]}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.stdout.strip()

    def screenshot(self, path=None, **kwargs):
        self._screenshot_paths.append(path)
        if path:
            try:
                Path(path).write_bytes(b"\xff\xd8\xff\xe0")
            except Exception:
                pass

    def locator(self, selector):
        return _LocatorStub(self, selector)

    def expect_file_chooser(self, **kwargs):
        return _FileChooserStub()


class _LocatorStub:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return _LocatorStub(self._page, self._selector)

    def count(self):
        return 0

    def is_checked(self):
        return False

    def click(self, **kw):
        pass

    def check(self, **kw):
        pass

    def fill(self, v, **kw):
        pass

    def evaluate(self, *a, **kw):
        return None


class _FileChooserStub:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def set_files(self, *a, **kw):
        pass


def is_available() -> bool:
    """True if jsdom is installed and the bridge works."""
    if _find_jsdom() is None:
        return False
    try:
        p = CorpusPage.from_html("<html><body>test</body></html>")
        r = p.evaluate("() => document.body.innerText")
        return "test" in str(r)
    except Exception:
        return False