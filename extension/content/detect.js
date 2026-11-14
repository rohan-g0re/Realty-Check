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

  // Flat text of all visible nodes with a position map back to nodes. Nodes are joined with `sep` (default none:
  // claim quotes are matched on the plain join; the address scan joins with a space so "<b>2111</b><b>Austin St</b>"
  // reads "2111 Austin St").
  RC.flatten = function flatten(nodes, sep = "") {
    const starts = [];
    let str = "";
    for (const n of nodes) { if (str && sep) str += sep; starts.push(str.length); str += n.nodeValue; }
    const locate = (pos, isEnd) => {
      let lo = 0, hi = nodes.length - 1;
      while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (starts[mid] < pos || (!isEnd && starts[mid] === pos)) lo = mid; else hi = mid - 1; }
      return { node: nodes[lo], offset: Math.max(0, pos - starts[lo]) };
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

  // Street suffixes in title case and in capitals ("2111 AUSTIN ST" is common in headings).
  const SUFFIX_W = "St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Pkwy|Parkway|Ter|Terrace|Cir|Circle|Hwy|Highway|Sq|Square|Pike|Plaza|Row|Trl|Trail|Loop|Fwy|Freeway|Expy".split("|");
  const SUFFIX = SUFFIX_W.concat(SUFFIX_W.map((w) => w.toUpperCase())).join("|");
  // State as two capitals, or spelled out when a ZIP follows ("Houston, Texas 77002").
  const STATE_ZIP = "(?:[A-Z]{2}\\s*|[A-Z][a-z]{3,}(?:\\s[A-Z][a-z]+)?\\s+)\\d{5}(?:-\\d{4})?";
  const CITY = "[A-Z][A-Za-z.'-]+(?:\\s[A-Z][A-Za-z.'-]+){0,3},?\\s*(?:" + STATE_ZIP + "|[A-Z]{2}\\b)";
  // House number not glued to a price, phone number or unit ("$2,111", "555-2111", "#2111"). Street words are
  // capitalised words or ordinals ("5th"), never a bare number: text nodes are joined with spaces, and a bare
  // number there is usually the next address ("2111 Austin" heading + "2111 Austin St" line). At least one street
  // word, and none of the listing words that sit next to numbers ("767 Sq Ft", "2 BEDS Santee Court").
  const NOT_STREET = "(?!(?:Beds?|BEDS?|Bedrooms?|BEDROOMS?|Baths?|BATHS?|Bathrooms?|BR|BA|Studios?|STUDIOS?|Sq|SQ|Units?|UNITS?|Floors?|FLOORS?|Stories|STORIES|Story|Homes?|Apartments?|Available|AVAILABLE)\\b)";
  const ADDR_RE = new RegExp(
    "(?<![\\d,.$#/-])(?<!\\b[A-Z]{2}\\s{1,2})\\b[1-9]\\d{0,5}[A-Z]?\\s+(?:[NSEW]\\.?\\s+)?(?:" + NOT_STREET + "(?:[A-Z][A-Za-z0-9'.-]*|\\d+(?:st|nd|rd|th|ST|ND|RD|TH))\\s+){1,3}(?:" + SUFFIX + ")\\b\\.?" +
      "(?:,?\\s+(?:Apt|Unit|Suite|Ste|#)\\.?\\s*[A-Za-z0-9-]+)?" +
      "(?:,\\s*" + CITY + ")?", "g");
  const HAS_CITY = /,\s*[A-Z]{2}\b|\d{5}$/;
  // City line after a street-only match, often in the next element: "Houston, TX 77002".
  const TAIL_RE = new RegExp("^[\\s,·|–-]{0,4}([A-Z][A-Za-z.'-]+(?:\\s[A-Z][A-Za-z.'-]+){0,3}\\s*,?\\s*" + STATE_ZIP + ")(?!\\d)");
  const norm = (s) => s.replace(/\s+/g, " ").replace(/[\s,.]+$/, "").trim();
  const normTail = (t) => norm(t).replace(/\s*,?\s*([A-Z]{2})\s*(\d{5}(?:-\d{4})?)$/, ", $1 $2");
  const MAX_SCAN = 120;

  // All parsed application/ld+json objects on the page.
  RC.jsonld = function jsonld() {
    const out = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
      try { const v = JSON.parse(s.textContent); (Array.isArray(v) ? v : [v]).forEach((x) => out.push(x)); } catch (e) { /* ignore bad JSON-LD */ }
    });
    return out;
  };

  const fromParts = (street, city, region, zip) => {
    street = norm(String(street));
    const tail = [city, [region, zip].filter(Boolean).join(" ")].filter(Boolean).join(", ");
    return { street: street.split(",")[0], full: tail && !HAS_CITY.test(street) ? street + ", " + tail : street };
  };

  function jsonldAddresses(objs) {
    const out = [];
    const visit = (o, depth) => {
      if (!o || typeof o !== "object" || depth > 8) return;
      if (Array.isArray(o)) return o.forEach((x) => visit(x, depth + 1));
      if (typeof o.streetAddress === "string" && o.streetAddress.trim()) out.push(fromParts(o.streetAddress, o.addressLocality, o.addressRegion, o.postalCode));
      else if (typeof o.address === "string" && /^\s*\d/.test(o.address)) out.push(fromParts(o.address));
      for (const k in o) if (o[k] && typeof o[k] === "object") visit(o[k], depth + 1);
    };
    objs.forEach((o) => visit(o, 0));
    return out;
  }

  // Addresses that are not (or not only) in the visible text: meta tags, microdata, and the page title.
  function metaAddresses() {
    const out = [];
    const meta = (n) => { const m = document.querySelector(`meta[property="${n}"],meta[name="${n}"]`); return m ? (m.getAttribute("content") || "").trim() : ""; };
    for (const p of ["og", "business:contact_data", "place:location"]) {
      const st = meta(p + ":street_address") || meta(p + ":street-address");
      if (st) out.push(fromParts(st, meta(p + ":locality"), meta(p + ":region"), meta(p + ":postal_code") || meta(p + ":postal-code")));
    }
    document.querySelectorAll('[itemprop="streetAddress"]').forEach((el) => {
      const st = (el.getAttribute("content") || el.textContent || "").trim();
      if (!/^\d/.test(st)) return;
      const box = el.closest('[itemtype*="PostalAddress"]') || el.parentElement;
      const g = (k) => { const e = box && box.querySelector(`[itemprop="${k}"]`); return e ? (e.getAttribute("content") || e.textContent || "").trim() : ""; };
      out.push(fromParts(st, g("addressLocality"), g("addressRegion"), g("postalCode")));
    });
    for (const t of [document.title, meta("og:title"), meta("description"), meta("og:description"), meta("twitter:title")]) {
      if (!t) continue;
      ADDR_RE.lastIndex = 0;
      let m;
      while ((m = ADDR_RE.exec(t))) out.push(fromParts(m[0]));
    }
    return out;
  }

  // "2111 Austin St" -> ["2111", "austin"]: the house number and the first real street word, for loose matching.
  const DIRS = new Set(["n", "s", "e", "w", "north", "south", "east", "west"]);
  const keyParts = (street) => {
    const w = String(street).toLowerCase().replace(/[,.]/g, " ").split(/\s+/).filter(Boolean);
    const num = w[0] && /^\d/.test(w[0]) ? w[0] : "";
    return [num, w.slice(1).find((x) => !DIRS.has(x)) || ""];
  };
  const sameStreet = (a, b) => {
    const [n1, w1] = keyParts(a), [n2, w2] = keyParts(b);
    return !!n1 && n1 === n2 && !!w1 && w1 === w2;
  };

  // Returns [{text, full, ranges:[Range], src}], the page's own address first, capped at 30.
  // Text nodes are joined with a space, so a house number in its own element still reads as part of the address.
  // An address found only in JSON-LD / meta tags / the title has no ranges unless its number + street word are
  // visible; then it is pickable in the drawer only.
  RC.scanAddresses = function scanAddresses(nodes) {
    // Our own report page (served by the local checker) is not a listing.
    if (/^(127\.0\.0\.1|localhost)$/.test(location.hostname) && /^\/(report|runs)\//.test(location.pathname)) return [];
    const sp = RC.flatten(nodes, " ");
    const map = new Map();
    let m, order = 0;
    ADDR_RE.lastIndex = 0;
    while ((m = ADDR_RE.exec(sp.str)) && map.size < MAX_SCAN) {
      const raw = m[0].replace(/[\s,.]+$/, "");
      const a = m.index;
      let b = a + raw.length;
      let full = norm(raw);
      if (!HAS_CITY.test(full)) {
        // Street only: join "City, ST 12345" that follows within the next ~80 chars (often the next element).
        const t = TAIL_RE.exec(sp.str.slice(b, b + 80));
        if (t) { full = full + ", " + normTail(t[1]); b = b + t[0].length; }
      }
      const text = norm(raw.split(",")[0]);
      const key = text.toLowerCase();
      if (!map.has(key)) map.set(key, { text, full, ranges: [], src: "page", order: order++, hits: 0, score: 0 });
      const e = map.get(key);
      e.hits++;
      if (full.length > e.full.length) e.full = full;
      if (e.ranges.length < 4) e.ranges.push(sp.range(a, b));
    }
    const extra = (list, src, bonus) => {
      const done = new Set();
      for (const j of list) {
        if (done.has(j.street.toLowerCase())) continue;
        done.add(j.street.toLowerCase());
        let hit = false;
        for (const e of map.values()) if (sameStreet(e.text, j.street)) {
          hit = true;
          if (!e.src.includes(src)) e.src += " + " + src;
          e.score += bonus;
          if (!HAS_CITY.test(e.full) && HAS_CITY.test(j.full)) e.full = j.full;
        }
        if (hit) continue;
        // Not visible as a whole address: highlight the house number + street word when they are on the page.
        const [num, word] = keyParts(j.street);
        let ranges = RC.findText(sp, j.street, 3);
        if (!ranges.length && num && word) {
          const re = new RegExp("(?<![\\d,.$#-])\\b" + esc(num) + "\\s+(?:[NSEW]\\.?\\s+)?" + esc(word) + "\\b", "gi");
          let mm;
          while ((mm = re.exec(sp.str)) && ranges.length < 3) ranges.push(sp.range(mm.index, mm.index + mm[0].length));
        }
        map.set(j.street.toLowerCase(), { text: j.street, full: j.full, ranges, src, order: ranges.length ? order++ : 1e6, hits: ranges.length, score: bonus });
      }
    };
    extra(jsonldAddresses(RC.jsonld()), "JSON-LD", 4);
    extra(metaAddresses(), "page info", 3);
    // The page's own address is usually in the URL, repeated, and near the top; nearby listings are not.
    const url = decodeURIComponent(location.pathname + " " + location.search).toLowerCase().replace(/[-_+/.]+/g, " ");
    for (const e of map.values()) {
      const [num, word] = keyParts(e.text);
      if (num && word && new RegExp("\\b" + esc(num) + " (?:[nsew] |north |south |east |west )?" + esc(word) + "\\b").test(url)) e.score += 2;
      e.score += Math.min(e.hits, 4) * 0.25;
    }
    // One entry per street address: "3663 Washington Ave" and "3663 Washington Ave. Suite 1000" are the same place.
    const merged = [];
    for (const e of [...map.values()].sort((x, y) => x.order - y.order)) {
      const o = merged.find((x) => sameStreet(x.text, e.text));
      if (!o) { merged.push(e); continue; }
      o.score = Math.max(o.score, e.score) + 0.25;
      if (HAS_CITY.test(e.full) && (!HAS_CITY.test(o.full) || e.full.length > o.full.length)) o.full = e.full;
      o.ranges = o.ranges.concat(e.ranges).slice(0, 6);
      if (!o.src.includes(e.src)) o.src += " + " + e.src;
    }
    // Street with no city line next to it on screen: the city often sits in a hidden copy (mobile header, footer).
    let all = null;
    for (const e of merged) {
      if (HAS_CITY.test(e.full)) continue;
      if (all === null) all = (document.body ? document.body.textContent : "").replace(/\s+/g, " ").slice(0, 500000);
      const i = all.toLowerCase().indexOf(e.text.toLowerCase());
      const t = i >= 0 && TAIL_RE.exec(all.slice(i + e.text.length, i + e.text.length + 80));
      if (t) e.full = e.full + ", " + normTail(t[1]);
    }
    return merged.sort((x, y) => y.score - x.score || x.order - y.order).slice(0, MAX_ADDR)
      .map(({ text, full, ranges, src }) => ({ text, full, ranges, src }));
  };
})();
