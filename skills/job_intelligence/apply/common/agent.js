/* agent.js — Injected before any page JS.
   Provides window.__opencode with framework detection, unified value setting,
   MutationObserver field discovery, and value change tracking.

   Runs once per page, persists across SPA navigations.
   Python calls via page.evaluate("window.__opencode.getFields()") etc.
*/
(function () {
  if (window.__opencode) return; // already injected
  var AGENT = (window.__opencode = {});

  // ── 1. Framework detection ───────────────────────────────────────────
  AGENT.detectFramework = function () {
    var fw = {};
    try {
      fw.react = !!(
        window.React ||
        window.__REACT_DEVTOOLS_GLOBAL_HOOK__ ||
        document.querySelector('[data-reactroot],[data-reactid]')
      );
    } catch (e) { fw.react = false; }
    try { fw.backbone = !!(window.Backbone || window.Marionette); } catch (e) { fw.backbone = false; }
    try { fw.angular = !!window.angular; } catch (e) { fw.angular = false; }
    try { fw.ember = !!window.Ember; } catch (e) { fw.ember = false; }
    try { fw.jquery = typeof jQuery !== "undefined"; } catch (e) { fw.jquery = false; }
    return fw;
  };
  AGENT.framework = AGENT.detectFramework();

  // ── 2. Value observer (tracks input value changes for DIAG) ──────────
  var _valueLog = [];
  var _maxLog = 100;
  AGENT._onValueChange = function (el, oldVal, newVal) {
    if (oldVal === newVal) return;
    _valueLog.push({
      selector: "#" + el.id || el.name || el.tagName,
      label: el.getAttribute("aria-label") || el.placeholder || el.id || el.name,
      oldVal: (oldVal || "").slice(0, 40),
      newVal: (newVal || "").slice(0, 40),
      ts: Date.now(),
      trusted: el._opencodeTrusted !== true,
    });
    if (_valueLog.length > _maxLog) _valueLog.shift();
    el._opencodeTrusted = false; // reset for next change
  };
  AGENT.drainValueLog = function () {
    var copy = _valueLog.slice();
    _valueLog = [];
    return copy;
  };

  // Patch input event listeners on body (captures all input events)
  document.addEventListener(
    "input",
    function (e) {
      var el = e.target;
      if (!el || !el.tagName) return;
      if (el.tagName !== "INPUT" && el.tagName !== "SELECT" && el.tagName !== "TEXTAREA") return;
      AGENT._onValueChange(el, el._lastVal, el.value);
      el._lastVal = el.value;
    },
    true
  );

  // ── 3. Unified value setter (framework-aware) ─────────────────────────
  AGENT.setValue = function (sel, val) {
    var el = document.querySelector(sel);
    if (!el) return { ok: false, error: "not found" };
    var tag = el.tagName;
    var fw = AGENT.framework;
    var oldVal = el.value;

    try {
      if (fw.jquery && typeof jQuery !== "undefined") {
        // jQuery .val() works on all elements (input, select, textarea, div).
        // Stores in jQuery cache + DOM, survives Backbone re-renders.
        jQuery(el).val(val).trigger("change").trigger("input");
      } else if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
        if (fw.react && tag === "INPUT") {
          // React: nativeValueSetter bypasses synthetic events
          var ns =
            Object.getOwnPropertyDescriptor(
              window.HTMLInputElement.prototype, "value"
            ).set;
          ns.call(el, val);
        } else {
          el.value = val;
        }
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      } else if (tag === "DIV" || el.getAttribute("contenteditable")) {
        // contenteditable divs: use textContent instead of value
        el.textContent = val;
        el.dispatchEvent(new Event("input", { bubbles: true }));
      } else {
        // Fallback for unknown elements
        el.value = val;
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }
      el._opencodeTrusted = true;
      el._lastVal = val;
    } catch (e) {
      // Last resort
      try { el.value = val; el._lastVal = val; } catch (ex) {}
    }

    return { ok: true, oldVal: oldVal, newVal: val };
  };

  // ── 4. Click helper with disabled re-enable ───────────────────────────
  AGENT.click = function (sel) {
    var el = document.querySelector(sel);
    if (!el) return { ok: false, error: "not found" };
    el.disabled = false; // re-enable if Backbone validation disabled it
    el.click();
    return { ok: true, tag: el.tagName, text: (el.textContent || el.value || "").slice(0, 30) };
  };

  // ── 5. MutationObserver for field discovery ───────────────────────────
  var _fieldCache = [];
  var _fieldDirty = true;
  AGENT._scanFields = function () {
    var results = [];
    var sel =
      'input:not([type=hidden]):not([type=submit]), select, textarea, [contenteditable="true"]';
    var seen = new Set();
    document.querySelectorAll(sel).forEach(function (el) {
      if (el.offsetParent === null && el.type !== "file") return;
      if (seen.has(el.id)) return;
      seen.add(el.id);
      results.push({
        tag: el.tagName,
        type: el.getAttribute("type") || "",
        id: el.id,
        name: el.getAttribute("name") || "",
        label: AGENT._resolveLabel(el),
        placeholder: el.placeholder || "",
        value: el.value || "",
        required: !!el.required || el.getAttribute("aria-required") === "true",
        maxlength: el.getAttribute("maxlength") || "",
        pattern: el.getAttribute("pattern") || "",
        selector: "#" + CSS.escape(el.id) || "",
        className: el.className || "",
      });
    });
    return results;
  };
  AGENT._resolveLabel = function (el) {
    var label = "";
    if (el.getAttribute("aria-labelledby")) {
      var ref = document.getElementById(el.getAttribute("aria-labelledby"));
      if (ref) label = ref.textContent.trim();
    }
    if (!label && el.getAttribute("aria-label")) label = el.getAttribute("aria-label");
    if (!label && el.computedName) { var cn = el.computedName.trim(); if (cn && cn.length < 100) label = cn; }
    if (!label) {
      var lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lbl) label = lbl.textContent.trim();
    }
    if (!label) {
      var p = el.closest("label");
      if (p) label = p.textContent.trim();
    }
    if (!label && el.placeholder) label = el.placeholder;
    if (!label) {
      var parent = el.closest("div,fieldset,section,li,form");
      var plbl = parent ? parent.querySelector("label, legend, strong, span") : null;
      if (plbl) label = plbl.textContent.trim();
    }
    return (label || "").replace(/\s+/g, " ").trim().slice(0, 80);
  };

  // Set up observer for dynamic field discovery
  var _mo = new MutationObserver(function () { _fieldDirty = true; });
  try {
    _mo.observe(document.body || document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["id", "name", "class", "type"],
    });
  } catch (e) { /* page may not be ready */ }

  AGENT.getFields = function () {
    if (_fieldDirty || !_fieldCache.length) {
      _fieldCache = AGENT._scanFields();
      _fieldDirty = false;
    }
    return _fieldCache;
  };
  AGENT.invalidateFields = function () { _fieldDirty = true; };

  // ── 6. Form submit detection ─────────────────────────────────────────
  // NOT monkeypatched here — uses Playwright's page.route() from Python to
  // intercept fetch/XHR without modifying page JS (no fingerprinting risk).
  // See agent_bridge.setup_network_interception(page) for Python-side setup.
  AGENT._submitLog = [];
  AGENT.drainSubmitLog = function () {
    var copy = AGENT._submitLog.slice();
    AGENT._submitLog = [];
    return copy;
  };
  // Called from Python after page.route intercepts a POST/PUT:
  AGENT.recordSubmit = function (entry) {
    AGENT._submitLog.push(entry);
    if (AGENT._submitLog.length > 50) AGENT._submitLog.shift();
  };

  // ── 7. Console error capture ─────────────────────────────────────────
  AGENT._consoleErrors = [];
  AGENT.drainConsoleErrors = function () {
    var copy = AGENT._consoleErrors.slice();
    AGENT._consoleErrors = [];
    return copy;
  };
  var _origConsoleErr = console.error;
  console.error = function () {
    var msg = Array.prototype.slice.call(arguments)
      .map(function (a) { return (typeof a === "string" ? a : (a && a.message) || String(a)).slice(0, 120); })
      .join(" ")
      .slice(0, 300);
    if (
      msg.toLowerCase().includes("required") ||
      msg.toLowerCase().includes("invalid") ||
      msg.toLowerCase().includes("validation") ||
      msg.toLowerCase().includes("field") ||
      msg.toLowerCase().includes("cannot be empty")
    ) {
      AGENT._consoleErrors.push({ msg: msg, ts: Date.now() });
      if (AGENT._consoleErrors.length > 50) AGENT._consoleErrors.shift();
    }
    return _origConsoleErr.apply(this, arguments);
  };
})();
