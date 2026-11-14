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
  // claim quotes are matched on the plain join; the address scan joins with a space so "<b>418</b><b>Linden Grove Ave</b>"
  // reads "418 Linden Grove Ave").
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

  // Street suffixes in title case and in capitals ("418 LINDEN GROVE AVE" is common in headings).
  const SUFFIX_W = "St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Pkwy|Parkway|Ter|Terrace|Cir|Circle|Hwy|Highway|Sq|Square|Pike|Plaza|Row|Trl|Trail|Loop|Fwy|Freeway|Expy".split("|");
  const SUFFIX = SUFFIX_W.concat(SUFFIX_W.map((w) => w.toUpperCase())).join("|");
  // State as two capitals, or spelled out when a ZIP follows ("Houston, Texas 77002").
  const STATE_ZIP = "(?:[A-Z]{2}\\s*|[A-Z][a-z]{3,}(?:\\s[A-Z][a-z]+)?\\s+)\\d{5}(?:-\\d{4})?";
  const CITY = "[A-Z][A-Za-z.'-]+(?:\\s[A-Z][A-Za-z.'-]+){0,3},?\\s*(?:" + STATE_ZIP + "|[A-Z]{2}\\b)";
  // House number not glued to a price, phone number or unit ("$2,111", "555-2111", "#2111"). Street words are
  // capitalised words or ordinals ("5th"), never a bare number: text nodes are joined with spaces, and a bare
  // number there is usually the next address ("418 Linden Grove" heading + "418 Linden Grove Ave" line). At least one street
  // word, and none of the listing words that sit next to numbers ("767 Sq Ft", "2 BEDS Alder Court").
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

  // JSON-LD @type of the thing an address belongs to: the building itself, or a company / agent.
  const RES_T = /^(ApartmentComplex|Apartment|Residence|SingleFamilyResidence|House|Accommodation|GatedResidenceCommunity|Place|RealEstateListing|Room|Suite|LodgingBusiness)$/i;
  const ORG_T = /^(Organization|Corporation|RealEstateAgent|Person|ProfessionalService|GovernmentOrganization|WebSite|WebPage)$/i;
  const BIZ_T = /^(LocalBusiness|HomeAndConstructionBusiness|Store)$/i;
  const typesOf = (o) => [].concat(o && o["@type"] || []).map(String);
  // Bonus for an address found in JSON-LD, by the type that owns it.
  const ldBonus = (types) => types.some((t) => RES_T.test(t)) ? 6
    : types.some((t) => ORG_T.test(t)) ? 1
      : types.some((t) => BIZ_T.test(t)) ? 2.5 : 4;

  function jsonldAddresses(objs) {
    const out = [];
    const visit = (o, depth, owner) => {
      if (!o || typeof o !== "object" || depth > 8) return;
      if (Array.isArray(o)) return o.forEach((x) => visit(x, depth + 1, owner));
      const own = typesOf(o).filter((t) => !/PostalAddress/i.test(t));
      const types = own.length ? own : owner;
      const add = (a) => out.push(Object.assign(a, { types, bonus: ldBonus(types) }));
      if (typeof o.streetAddress === "string" && o.streetAddress.trim()) add(fromParts(o.streetAddress, o.addressLocality, o.addressRegion, o.postalCode));
      else if (typeof o.address === "string" && /^\s*\d/.test(o.address)) add(fromParts(o.address));
      for (const k in o) if (o[k] && typeof o[k] === "object") visit(o[k], depth + 1, types);
    };
    objs.forEach((o) => visit(o, 0, []));
    // Best owner first, so one street found under both ApartmentComplex and Organization keeps the building's bonus.
    return out.sort((a, b) => b.bonus - a.bonus);
  }

  // Addresses in map links ("Get directions") and map embeds: ?q=, ?daddr=, ?destination=, ?query=.
  const MAP_SEL = 'a[href*="maps.google"],a[href*="google.com/maps"],a[href*="goo.gl/maps"],a[href*="maps.apple"],a[href*="bing.com/maps"],iframe[src*="google.com/maps"],iframe[src*="maps.google"]';
  function mapAddresses() {
    const out = [];
    document.querySelectorAll(MAP_SEL).forEach((el) => {
      let u;
      try { u = new URL(el.getAttribute("href") || el.getAttribute("src") || "", location.href); } catch (e) { return; }
      for (const k of ["q", "daddr", "destination", "query", "address"]) {
        const v = (u.searchParams.get(k) || "").replace(/\+/g, " ").trim();
        if (!v) continue;
        ADDR_RE.lastIndex = 0;
        const m = ADDR_RE.exec(v);
        if (!m) continue;
        let full = m[0];
        const t = !HAS_CITY.test(norm(full)) && TAIL_RE.exec(v.slice(m.index + m[0].length));
        if (t) full = norm(full) + ", " + normTail(t[1]);
        out.push(Object.assign(fromParts(full), { el }));
        break;
      }
    });
    return out;
  }

  // ---------- where on the page an address sits ----------
  const FOOT_CLS = /footer|colophon|copyright|site-?info|(^|[\s_-])foot([\s_-]|$)/i;
  const NEAR_CLS = /nearby|similar|related|recommend|also-?like|recently-?viewed|comparable|other-?(listings?|properties|homes|apartments|communities|rentals)|more-?(listings?|properties|homes|apartments|rentals)/i;
  const HERO_CLS = /hero|masthead|jumbotron|(property|listing|building|community|unit)[-_]?(header|title|name|info|summary|details|address|overview|top)/i;
  const NEAR_HEAD = /\b(nearby|similar|you (may|might) also like|more (apartments|homes|rentals|listings|properties|places)|other (apartments|homes|listings|properties|communities|rentals)|recently viewed|related (listings|properties)|comparable|people also (viewed|looked)|explore (more|other|nearby))\b/i;
  // Words next to an address saying whose it is.
  const LBL_CORP = /corporate|headquarter|\bhq\b|head office|home office|main office|regional office|managed by|management (company|office|group)|professionally managed|mailing address|billing address|company address|our offices?\b|developed by|owned by/i;
  const LBL_LEASE = /leasing (office|center|centre)|sales (office|center|centre|gallery)|showroom/i;
  const LBL_HERE = /located at|located in|visit us|property address|community address|building address|find us|get directions|directions to|welcome (home )?to|you'll live|live at/i;
  const clsOf = (e) => ((typeof e.className === "string" ? e.className : (e.getAttribute && e.getAttribute("class")) || "") + " " + (e.id || ""));

  // Region tags for one element: h1, hero, header, main, footer, nearby, map.
  const regionCache = new WeakMap();
  function regionOf(el) {
    if (!el) return [];
    if (regionCache.has(el)) return regionCache.get(el);
    const tags = new Set();
    if (el.closest("h1")) tags.add("h1");
    if (el.closest("footer,[role=contentinfo]")) tags.add("footer");
    if (el.closest("header,[role=banner]")) tags.add("header");
    if (el.closest("main,[role=main],article")) tags.add("main");
    if (el.closest(MAP_SEL)) tags.add("map");
    for (let e = el, d = 0; e && e !== document.body && e !== document.documentElement && d < 14; e = e.parentElement, d++) {
      const ci = clsOf(e);
      if (FOOT_CLS.test(ci)) tags.add("footer");
      if (NEAR_CLS.test(ci)) tags.add("nearby");
      if (HERO_CLS.test(ci)) tags.add("hero");
      // A "Similar apartments" / "Nearby" heading just before this block, or heading its own section/aside.
      if (d < 8 && !tags.has("nearby") && !/^(MAIN|ARTICLE|BODY)$/.test(e.tagName)) {
        let s = e.previousElementSibling;
        for (let i = 0; s && i < 2; i++, s = s.previousElementSibling) {
          const h = /^H[2-6]$/.test(s.tagName) ? s : s.querySelector && s.querySelector("h2,h3,h4,h5");
          if (h && (h.textContent || "").length < 80 && NEAR_HEAD.test(h.textContent)) { tags.add("nearby"); break; }
        }
        if (/^(SECTION|ASIDE)$/.test(e.tagName)) {
          const h = e.querySelector("h2,h3,h4,h5");
          if (h && (h.textContent || "").length < 80 && NEAR_HEAD.test(h.textContent)) tags.add("nearby");
        }
      }
      if (/^(MAIN|ARTICLE)$/.test(e.tagName)) break;
    }
    const out = [...tags];
    regionCache.set(el, out);
    return out;
  }

  // Short label text right before an address: same block up to the address, plus a short previous heading/label.
  function labelOf(el, street) {
    const block = el.closest("p,li,address,dd,td,div,section,footer,aside,header") || el;
    const t = (block.textContent || "").replace(/\s+/g, " ");
    const num = (String(street).match(/^\d+/) || [""])[0];
    const i = num ? t.indexOf(num) : -1;
    let lbl = (i > 0 ? t.slice(Math.max(0, i - 100), i) : "");
    const prev = block.previousElementSibling;
    if (prev && (prev.textContent || "").trim().length < 80) lbl = prev.textContent + " " + lbl;
    return lbl;
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

  // "418 Linden Grove Ave" -> ["418", "linden"]: the house number and the first real street word, for loose matching.
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
      if (!map.has(key)) map.set(key, { text, full, ranges: [], src: "page", order: order++, hits: 0, score: 0, where: new Set(), spots: [], label: "" });
      const e = map.get(key);
      e.hits++;
      if (full.length > e.full.length) e.full = full;
      const r = e.ranges.length < 4 || e.spots.length < 12 ? sp.range(a, b) : null;
      if (r && e.ranges.length < 4) e.ranges.push(r);
      if (r && e.spots.length < 12) {
        // Where this occurrence sits, and the words right before it.
        const el = r.startContainer.parentElement;
        e.spots.push(regionOf(el));
        if (e.label.length < 400) e.label += " | " + labelOf(el, text);
      }
    }
    // `tagOf(j)` gives the where-tag for an entry ("jsonld:ApartmentComplex", "meta", "map").
    const extra = (list, src, bonus, tagOf) => {
      const done = new Set();
      for (const j of list) {
        if (done.has(j.street.toLowerCase())) continue;
        done.add(j.street.toLowerCase());
        const b = j.bonus != null ? j.bonus : bonus;
        const tag = tagOf(j);
        let hit = false;
        for (const e of map.values()) if (sameStreet(e.text, j.street)) {
          hit = true;
          if (!e.src.includes(src)) e.src += " + " + src;
          e.score += b;
          e.where.add(tag);
          if (j.el) for (const t of regionOf(j.el)) e.where.add(t);
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
        const spots = ranges.map((r) => regionOf(r.startContainer.parentElement));
        if (j.el) spots.push(regionOf(j.el));
        const label = ranges.map((r) => labelOf(r.startContainer.parentElement, j.street)).join(" | ");
        map.set(j.street.toLowerCase(), { text: j.street, full: j.full, ranges, src, order: ranges.length ? order++ : 1e6, hits: ranges.length, score: b, where: new Set([tag]), spots, label });
      }
    };
    const ldType = (j) => "jsonld:" + ((j.types || []).find((t) => RES_T.test(t)) || (j.types || [])[0] || "Thing");
    extra(jsonldAddresses(RC.jsonld()), "JSON-LD", 4, ldType);
    extra(metaAddresses(), "page info", 3, () => "meta");
    extra(mapAddresses(), "map link", 1, () => "map");
    // The page's own address is usually in the URL, repeated, and near the top; nearby listings are not.
    const url = decodeURIComponent(location.pathname + " " + location.search).toLowerCase().replace(/[-_+/.]+/g, " ");
    for (const e of map.values()) {
      const [num, word] = keyParts(e.text);
      if (num && word && new RegExp("\b" + esc(num) + " (?:[nsew] |north |south |east |west )?" + esc(word) + "\b").test(url)) { e.score += 2; e.where.add("url"); }
      e.score += Math.min(e.hits, 4) * 0.25;
    }
    // One entry per street address: "5120 Halvorsen Ave" and "5120 Halvorsen Ave. Suite 1000" are the same place.
    const merged = [];
    for (const e of [...map.values()].sort((x, y) => x.order - y.order)) {
      const o = merged.find((x) => sameStreet(x.text, e.text));
      if (!o) { merged.push(e); continue; }
      o.score = Math.max(o.score, e.score) + 0.25;
      if (HAS_CITY.test(e.full) && (!HAS_CITY.test(o.full) || e.full.length > o.full.length)) o.full = e.full;
      o.ranges = o.ranges.concat(e.ranges).slice(0, 6);
      if (!o.src.includes(e.src)) o.src += " + " + e.src;
      for (const t of e.where) o.where.add(t);
      o.spots = o.spots.concat(e.spots);
      o.label += " | " + e.label;
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
    merged.forEach(placeScore);
    return merged.sort((x, y) => y.score - x.score || x.order - y.order).slice(0, MAX_ADDR)
      .map(({ text, full, ranges, src, score, where, why }) => ({ text, full, ranges, src, score: Math.round(score * 100) / 100, where: [...where], why }));
  };

  // Words of the page's own name: title, h1 and URL path (for "the street named like the page").
  const STOP = new Set(["the", "and", "for", "apartments", "apartment", "apts", "homes", "home", "rent", "rentals", "rental", "for", "in", "at", "of", "a", "to", "st", "ave", "rd", "dr", "blvd", "street", "avenue", "road", "drive", "lane", "way", "north", "south", "east", "west", "tx", "ca", "ny"]);
  const words = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").split(" ").filter((w) => w.length > 2 && !STOP.has(w) && !/^\d+$/.test(w));
  function pageWords() {
    const h1 = document.querySelector("h1");
    return new Set(words(document.title + " " + (h1 ? h1.textContent : "") + " " + decodeURIComponent(location.pathname).replace(/[-_/.]+/g, " ")));
  }

  // Context score on top of the source bonuses: where the address sits and what it is labelled as.
  // A footer address is kept (on a building's own site it is often the real address) but needs other support.
  function placeScore(e) {
    const why = [];
    const w = e.where;
    const spots = e.spots;
    const any = (t) => spots.some((s) => s.includes(t));
    const only = (t) => spots.length > 0 && spots.every((s) => s.includes(t));
    for (const t of ["h1", "hero", "header", "main", "footer", "nearby", "map"]) if (any(t)) w.add(t);
    const ld = [...w].filter((t) => t.startsWith("jsonld:")).map((t) => t.slice(7));
    const resLd = ld.some((t) => RES_T.test(t));
    if (resLd) why.push("given as the property's address in the page data");
    else if (ld.some((t) => ORG_T.test(t))) why.push("the page data gives it as a company's address");
    else if (ld.some((t) => BIZ_T.test(t))) why.push("given as the business address in the page data");
    if (w.has("meta")) why.push("in the page's info tags");
    if (w.has("url")) why.push("matches the web address");
    if (any("h1")) { e.score += 2; why.push("in the main heading"); }
    else if (any("hero")) { e.score += 1.5; why.push("at the top of the listing"); }
    if (any("main") && !any("nearby")) e.score += 0.5;
    if (w.has("map")) { e.score += 0.5; why.push("linked to a map"); }
    const [, word] = keyParts(e.text);
    if (word && word.length > 2 && !/^\d/.test(word) && pageWords().has(word)) { e.score += 0.75; e.where.add("title-words"); why.push("its street is named in the page title"); }
    const lbl = e.label || "";
    if (LBL_CORP.test(lbl)) { e.score -= 2.5; w.add("label:corporate"); why.push("labelled as a company or office address"); }
    else if (LBL_LEASE.test(lbl)) { e.score -= 0.75; w.add("label:leasing"); why.push("labelled as a leasing office"); }
    if (LBL_HERE.test(lbl)) { e.score += 1; w.add("label:located"); why.push("labelled as where the place is"); }
    if (only("nearby")) { e.score -= 3; why.push("in a nearby or similar listings section"); }
    if (only("footer")) {
      const support = resLd || w.has("url") || w.has("meta") || w.has("title-words") || any("h1") || any("hero");
      e.score -= support ? 0.5 : 2;
      why.push("only in the page footer");
    }
    if (!why.length) why.push("found in the page text");
    e.why = why[0].charAt(0).toUpperCase() + why.join(", ").slice(1) + ".";
  }

  // Top candidates for the snapshot's `address_candidates` (best first): [{text, score, where, why}].
  RC.addressCandidates = function addressCandidates(list, n = 5) {
    return (list || []).slice(0, n).map((a) => ({ text: a.full || a.text, score: a.score, where: a.where || [], why: a.why || "" }));
  };
})();
