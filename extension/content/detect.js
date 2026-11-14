// Reality Check: site-agnostic address + phrase detection. Reads the page; never edits it.
(() => {
  const RC = (globalThis.RC = globalThis.RC || {});
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "TEXTAREA", "INPUT", "SELECT", "OPTION", "SVG", "CANVAS", "IFRAME", "CODE", "PRE", "BUTTON"]);
  const MAX_ADDR = 30;

  // Visible text nodes in document order, skipping form fields, scripts and our own UI.
  RC.textNodes = function textNodes() {
    const out = [];
    if (!document.body) return out;
    const vis = new WeakMap();
    const visible = (el) => {
      if (vis.has(el)) return vis.get(el);
      let v;
      if (el.checkVisibility) v = el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
      else { const cs = getComputedStyle(el); v = cs.display !== "none" && cs.visibility !== "hidden"; }
      vis.set(el, v);
      return v;
    };
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        if (!n.nodeValue || !n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        const p = n.parentElement;
        if (!p || p.closest("#rc-host") || p.isContentEditable) return NodeFilter.FILTER_REJECT;
        for (let e = p; e && e !== document.body; e = e.parentElement) if (SKIP.has(e.tagName.toUpperCase())) return NodeFilter.FILTER_REJECT;
        return visible(p) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    while (w.nextNode()) out.push(w.currentNode);
    return out;
  };

  // Flat text of all visible nodes (joined without separators) with a position map back to nodes.
  RC.flatten = function flatten(nodes) {
    const starts = [];
    let str = "";
    for (const n of nodes) { starts.push(str.length); str += n.nodeValue; }
    const locate = (pos, isEnd) => {
      let lo = 0, hi = nodes.length - 1;
      while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (starts[mid] < pos || (!isEnd && starts[mid] === pos)) lo = mid; else hi = mid - 1; }
      return { node: nodes[lo], offset: pos - starts[lo] };
    };
    const range = (a, b) => {
      const s = locate(a, false), e = locate(b, true);
      const r = document.createRange();
      r.setStart(s.node, Math.min(s.offset, s.node.nodeValue.length));
      r.setEnd(e.node, Math.min(e.offset, e.node.nodeValue.length));
      return r;
    };
    return { str, starts, range };
  };

  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // Ranges where `phrase` appears in visible text (case-insensitive, flexible whitespace, may cross elements).
  RC.findText = function findText(flat, phrase, limit = 3) {
    const out = [];
    const p = String(phrase || "").trim();
    if (p.length < 2) return out;
    const re = new RegExp(p.split(/\s+/).map(esc).join("\\s*"), "gi");
    let m;
    while ((m = re.exec(flat.str)) && out.length < limit) {
      if (!m[0].length) { re.lastIndex++; continue; }
      out.push(flat.range(m.index, m.index + m[0].length));
    }
    return out;
  };

  const SUFFIX = "St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Pkwy|Parkway|Ter|Terrace|Cir|Circle|Hwy|Highway|Sq|Square|Pike|Plaza|Row|Trl|Trail|Loop|Fwy|Freeway|Expy|Walk";
  const CITY = "[A-Z][A-Za-z.'-]+(?:\\s[A-Z][A-Za-z.'-]+){0,3},?\\s*[A-Z]{2}(?:\\s+\\d{5}(?:-\\d{4})?)?";
  const ADDR_RE = new RegExp(
    "\\b\\d{1,6}[A-Z]?\\s+(?:[NSEW]\\.?\\s+)?(?:[A-Z0-9][A-Za-z0-9'.-]*\\s+){0,3}(?:" + SUFFIX + ")\\b\\.?" +
      "(?:,?\\s+(?:Apt|Unit|Suite|Ste|#)\\.?\\s*[A-Za-z0-9-]+)?" +
      "(?:,\\s*" + CITY + ")?", "g");
  const HAS_CITY = /,\s*[A-Z]{2}\b|\d{5}$/;
  // City line after a street-only match. Text nodes are joined without separators and whitespace-only nodes
  // are dropped, so "<span>CA</span> <span>90034</span>" reads "CA90034": allow no space before the ZIP.
  const TAIL_RE = new RegExp("^[\\s,·|–-]{0,4}([A-Z][A-Za-z.'-]+(?:\\s[A-Z][A-Za-z.'-]+){0,3},?\\s*[A-Z]{2}\\s*\\d{5}(?:-\\d{4})?)(?!\\d)");
  const normTail = (t) => norm(t).replace(/,?\s*([A-Z]{2})\s*(\d{5}(?:-\d{4})?)$/, ", $1 $2");
  const norm = (s) => s.replace(/\s+/g, " ").replace(/[\s,.]+$/, "").trim();

  // All parsed application/ld+json objects on the page.
  RC.jsonld = function jsonld() {
    const out = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
      try { const v = JSON.parse(s.textContent); (Array.isArray(v) ? v : [v]).forEach((x) => out.push(x)); } catch (e) { /* ignore bad JSON-LD */ }
    });
    return out;
  };

  function jsonldAddresses(objs) {
    const out = [];
    const visit = (o, depth) => {
      if (!o || typeof o !== "object" || depth > 8) return;
      if (Array.isArray(o)) return o.forEach((x) => visit(x, depth + 1));
      const a = o.address;
      if (a && typeof a === "object" && a.streetAddress) {
        const tail = [a.addressLocality, [a.addressRegion, a.postalCode].filter(Boolean).join(" ")].filter(Boolean).join(", ");
        out.push({ street: String(a.streetAddress), full: tail ? a.streetAddress + ", " + tail : String(a.streetAddress) });
      } else if (typeof a === "string") out.push({ street: a, full: a });
      for (const k in o) if (o[k] && typeof o[k] === "object") visit(o[k], depth + 1);
    };
    objs.forEach((o) => visit(o, 0));
    return out;
  }

  // Returns [{text, full, ranges:[Range], src}] capped at 30.
  RC.scanAddresses = function scanAddresses(nodes, flat) {
    const map = new Map();
    nodes.forEach((n, i) => {
      ADDR_RE.lastIndex = 0;
      let m;
      while ((m = ADDR_RE.exec(n.nodeValue)) && map.size < MAX_ADDR) {
        const raw = m[0].replace(/[\s,.]+$/, "");
        const a = flat.starts[i] + m.index;
        let b = a + raw.length;
        let full = norm(raw);
        if (!HAS_CITY.test(full)) {
          // Street only: join "City, ST 12345" that follows within the next ~80 chars (often the next element).
          const t = TAIL_RE.exec(flat.str.slice(b, b + 80));
          if (t) { full = full + ", " + normTail(t[1]); b = b + t[0].length; }
        }
        const key = norm(raw).toLowerCase();
        if (!map.has(key)) map.set(key, { text: norm(raw), full, ranges: [], src: "page" });
        const e = map.get(key);
        if (full.length > e.full.length) e.full = full;
        if (e.ranges.length < 4) e.ranges.push(flat.range(a, b));
      }
    });
    for (const j of jsonldAddresses(RC.jsonld())) {
      const s = j.street.toLowerCase();
      let hit = false;
      for (const e of map.values()) if (e.text.toLowerCase().startsWith(s) || s.startsWith(e.text.toLowerCase())) {
        hit = true; e.src = "page + JSON-LD";
        if (!HAS_CITY.test(e.full) && j.full.length > e.full.length) e.full = j.full;
      }
      if (!hit && map.size < MAX_ADDR) {
        const rs = RC.findText(flat, j.street, 3);
        if (rs.length) map.set(s, { text: norm(j.street), full: j.full, ranges: rs, src: "JSON-LD" });
      }
    }
    return [...map.values()];
  };
})();
