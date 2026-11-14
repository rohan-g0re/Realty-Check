// Realty Check: edge tab, drawer and run flow. Everything lives in ONE shadow root on <div id="rc-host">.
// The drawer has two states: Running (clock, claim field, step list) and Result (headline, counts, field, top findings).
(() => {
  if (window.top !== window || document.getElementById("rc-host")) return;
  const RC = globalThis.RC;
  const W = 420;
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const INK = "#111111", BLUE = "#1a4be0";
  // Page marks: one blue for "not true" and "close", ink for "true", grey for "couldn't check".
  const VC = { contradicted: BLUE, close: BLUE, confirmed: INK, cant_check: "#b5b5b5" };
  const SOURCES = ["record", "scan", "aerial", "county"];
  const SRC = { record: "CoStar", scan: "Matterport", aerial: "Aerial", county: "County" };
  const AG = { orchestrator: "Listing", claims: "Listing", matcher: "CoStar", inside: "Matterport", outside: "Aerial", paper: "County", judge: "Judging" };
  const AG_SRC = { CoStar: "record", Matterport: "scan", Aerial: "aerial", County: "county" };
  const BAD = new Set(["contradicted", "close"]);
  const UNIT_KINDS = new Set(["bedrooms", "bathrooms", "area_sqft"]);
  // Kinds where one source's own number can be compared with the listing's without the judge's plan/scan rules.
  const COMPARABLE = new Set(["stories", "year_built", "unit_count"]);
  const LOGO = `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><circle cx="12" cy="12" r="9.5" fill="none" stroke="#111" stroke-width="1.6"/><path d="M7.8 12.4l2.9 2.9 5.6-5.9" fill="none" stroke="#111" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const GEAR = `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>`;
  const CLOSE = `<svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3 3l10 10M13 3L3 13"/></svg>`;
  const CHEV = `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>`;
  const X = `<svg viewBox="0 0 12 12" width="8" height="8" aria-hidden="true"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const send = (msg) => { try { chrome.runtime.sendMessage(msg); } catch (e) { /* extension reloaded */ } };
  const num = (x) => (typeof x === "number" ? x : Number(String(x == null ? "" : x).replace(/,/g, "")));

  // ---------- host + shadow ----------
  const host = document.createElement("div");
  host.id = "rc-host";
  host.setAttribute("style", "all:initial !important;display:block !important;position:absolute !important;top:0 !important;left:0 !important;width:0 !important;height:0 !important;overflow:visible !important;z-index:2147483647 !important;");
  document.documentElement.appendChild(host);
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>${CSS()}${RC.OVERLAY_CSS}</style>
  <div class="tab" id="tab">
    <button class="tab-close" id="tabClose" aria-label="Hide on this page" title="Hide on this page">${X}</button>
    <div class="tab-handle" id="tabHandle" title="Drag to move"><i></i><i></i><i></i><i></i><i></i><i></i></div>
    <button class="tab-face" id="tabFace" aria-label="Open Realty Check" title="Realty Check">${LOGO}</button>
  </div>
  <aside class="drawer" id="drawer" aria-label="Realty Check">
    <header class="hd">
      <span class="brand">Realty Check</span>
      <div class="act">
        <button class="icon" id="btnGear" aria-label="Checker address" aria-expanded="false" title="Checker address">${GEAR}</button>
        <button class="icon" id="btnCollapse" aria-label="Close" title="Close">${CLOSE}</button>
      </div>
    </header>
    <div class="menu" id="menu" hidden>
      <label for="srv">Server address</label>
      <div class="menu-row"><input id="srv" spellcheck="false" autocomplete="off"><button class="btn secondary" id="srvSave">Save</button></div>
      <p class="hint" id="srvHint">Where the local checker runs.</p>
    </div>
    <div class="body" id="body"></div>
    <footer class="ft" id="ft" hidden>
      <button class="btn primary" id="btnFull">Open the full report</button>
      <div class="ftl" id="ftl"></div>
    </footer>
  </aside>`;
  const $ = (id) => root.getElementById(id);
  const tab = $("tab"), drawer = $("drawer"), body = $("body");

  // ---------- state ----------
  let addresses = [], flat = null;
  let run = null; // {seq, address, mode, locked, pick, cache, claims, verdicts, evidence, match, steps, t0, t1, fillIn, done, field, unreachable, error, token, pickOpen}
  let open = false, seq = 0, stepSeq = 0;
  const ORDER = { contradicted: 0, close: 1, confirmed: 2, cant_check: 9 };
  let fiOpen = false; // "Not on the listing" expanded
  const FI_TOP = 4, FIND_TOP = 3;
  const LOW_PICK = 0.5; // below this the address pick is a guess: say so and open the picker
  const imgCache = new Map(); // /img/<token> -> data URL ("" = failed, null = in flight)

  const overlay = RC.createOverlay(root, host, {
    onInspect: (a) => { openDrawer(); startRun(a, { locked: true }); }, // the person clicked this address
    onMark: (cid) => { openDrawer(); pointAt(cid); },
  });

  // ---------- detection ----------
  function scan() {
    const nodes = RC.textNodes();
    flat = RC.flatten(nodes);
    addresses = RC.scanAddresses(nodes, flat);
    host.dataset.count = String(addresses.length);
    overlay.setAddresses(addresses);
    rebuildMarks();
    if (open && !run) render(true);
  }
  function rebuildMarks() {
    // Several claims can share one quote ("2 Beds · 1 Bath · 855 sq ft"): draw ONE mark per quote,
    // coloured by its worst verdict, with every claim in the tooltip.
    const groups = new Map();
    if (run && flat) for (const c of run.claims) {
      const v = run.verdicts[c.id];
      // metadata claims carry a plain phrase ("2 bedrooms"), not page words: nothing on the page to mark
      if (!v || !c.quote || c.origin === "metadata") continue;
      const tip = v.verdict === "cant_check"
        ? `Listing says ${v.claimed}. We couldn't check this.`
        : `Listing says ${v.claimed}. ${SRC[v.source] || "We"} ${SRC[v.source] ? "shows" : "found"} ${v.found}.`;
      const g = groups.get(c.quote);
      if (!g) groups.set(c.quote, { claimId: c.id, claimIds: [c.id], verdict: v.verdict, tips: [tip] });
      else {
        g.tips.push(tip); g.claimIds.push(c.id);
        if ((ORDER[v.verdict] ?? 9) < (ORDER[g.verdict] ?? 9)) { g.verdict = v.verdict; g.claimId = c.id; }
      }
    }
    const marks = [];
    for (const [quote, g] of groups) {
      const tip = g.tips.join(" ");
      for (const range of RC.findText(flat, quote, 3)) marks.push({ range, claimId: g.claimId, claimIds: g.claimIds, verdict: g.verdict, color: VC[g.verdict] || VC.cant_check, tip });
    }
    overlay.setMarks(marks);
  }

  // ---------- edge tab ----------
  let tabFrac = 0.5;
  const placeTab = () => { tab.style.top = Math.max(8, Math.min(innerHeight - 52, tabFrac * innerHeight - 22)) + "px"; };
  try { chrome.storage.local.get("rcTabY", (r) => { if (r && typeof r.rcTabY === "number") tabFrac = r.rcTabY; placeTab(); }); } catch (e) { /* no storage */ }
  placeTab();
  addEventListener("resize", placeTab, { passive: true });
  const hideKey = "rc-hidden:" + location.pathname;
  try { if (sessionStorage.getItem(hideKey)) tab.classList.add("gone"); } catch (e) { /* blocked */ }
  $("tabFace").addEventListener("click", () => openDrawer());
  $("tabClose").addEventListener("click", (e) => {
    e.stopPropagation(); tab.classList.add("gone");
    try { sessionStorage.setItem(hideKey, "1"); } catch (err) { /* blocked */ }
  });
  $("tabHandle").addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const h = e.currentTarget; h.setPointerCapture(e.pointerId);
    const y0 = e.clientY, t0 = parseFloat(tab.style.top);
    tab.classList.add("dragging");
    const mv = (ev) => { const t = Math.max(8, Math.min(innerHeight - 52, t0 + ev.clientY - y0)); tab.style.top = t + "px"; tabFrac = (t + 22) / innerHeight; };
    const up = () => {
      tab.classList.remove("dragging"); h.removeEventListener("pointermove", mv); h.removeEventListener("pointerup", up);
      try { chrome.storage.local.set({ rcTabY: tabFrac }); } catch (err) { /* no storage */ }
    };
    h.addEventListener("pointermove", mv); h.addEventListener("pointerup", up);
  });

  // ---------- drawer + page push ----------
  const de = document.documentElement;
  let saved = null, fixedSaved = [];
  const getProp = (el, p) => [el.style.getPropertyValue(p), el.style.getPropertyPriority(p)];
  const setProp = (el, p, [v, pr]) => { if (v) el.style.setProperty(p, v, pr); else el.style.removeProperty(p); };
  function pushFixed() {
    // Best effort: shrink the site's own full-width fixed bars so they don't sit under the drawer.
    fixedSaved = [];
    const all = document.body ? document.body.querySelectorAll("*") : [];
    const vw = innerWidth;
    for (let i = 0; i < all.length && i < 4000; i++) {
      const el = all[i];
      if (getComputedStyle(el).position !== "fixed") continue;
      const r = el.getBoundingClientRect();
      if (r.right < vw - 20 || r.width < vw / 2) continue;
      fixedSaved.push([el, getProp(el, "width"), getProp(el, "max-width"), getProp(el, "right")]);
      el.style.setProperty("width", Math.max(0, r.width - W) + "px", "important");
      el.style.setProperty("max-width", Math.max(0, r.width - W) + "px", "important");
    }
  }
  function unpushFixed() {
    for (const [el, w, mw] of fixedSaved) { setProp(el, "width", w); setProp(el, "max-width", mw); }
    fixedSaved = [];
  }
  function openDrawer() {
    if (open) return;
    open = true;
    saved = { mr: getProp(de, "margin-right"), tr: getProp(de, "transition") };
    if (!REDUCED) de.style.setProperty("transition", "margin-right .36s cubic-bezier(.2,.8,.2,1)", "important");
    de.style.setProperty("margin-right", W + "px", "important");
    pushFixed();
    drawer.classList.add("open");
    tab.classList.add("away");
    render(true);
    overlay.follow(420);
  }
  function closeDrawer() {
    if (!open) return;
    open = false;
    de.style.setProperty("margin-right", "0px", "important");
    unpushFixed();
    drawer.classList.remove("open");
    tab.classList.remove("away");
    $("menu").hidden = true;
    $("btnGear").setAttribute("aria-expanded", "false");
    setTimeout(() => {
      if (open) return;
      setProp(de, "margin-right", saved.mr); setProp(de, "transition", saved.tr);
      overlay.layout();
    }, REDUCED ? 0 : 380);
    overlay.follow(420);
    try { $("tabFace").focus({ preventScroll: true }); } catch (e) { /* not focusable */ }
  }
  $("btnCollapse").addEventListener("click", closeDrawer);
  drawer.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  $("btnGear").addEventListener("click", () => {
    const m = $("menu");
    m.hidden = !m.hidden;
    $("btnGear").setAttribute("aria-expanded", String(!m.hidden));
    if (!m.hidden) try { chrome.storage.local.get("rcServer", (r) => { $("srv").value = (r && r.rcServer) || "http://127.0.0.1:8787"; }); } catch (e) { /* no storage */ }
  });
  $("srvSave").addEventListener("click", () => {
    const v = $("srv").value.trim().replace(/\/+$/, "") || "http://127.0.0.1:8787";
    try { chrome.storage.local.set({ rcServer: v }, () => { $("srvHint").textContent = "Saved."; setTimeout(() => { $("srvHint").textContent = "Where the local checker runs."; }, 1500); }); } catch (e) { /* no storage */ }
  });
  const openReport = (hash) => {
    if (!run || !run.done) return;
    hash = typeof hash === "string" ? hash : "";
    if (run.mode === "sample") send({ type: "openSampleReport", address: run.address.full, hash });
    else send({ type: "openReport", report_url: (run.done.report_url || "/") + hash });
  };
  $("btnFull").addEventListener("click", () => openReport());
  $("ft").addEventListener("click", (e) => {
    const walk = e.target.closest("#btnWalk");
    if (walk && run && run.done && run.mode === "server") {
      // The report, at this finding: its live 3D viewer is embedded there and opens at the spot by itself.
      openReport("#claim-" + encodeURIComponent(walk.dataset.id));
      return;
    }
    if (e.target.closest("#btnAnother")) { stopRun(); run = null; rebuildMarks(); overlay.setSelected(null); render(true); }
  });

  // ---------- run ----------
  let port = null;
  function stopRun() {
    if (port) { try { port.disconnect(); } catch (e) { /* closed */ } port = null; }
    if (run) run.token = null;
  }
  const newRun = (a, mode, step) => ({
    seq: ++seq, address: a, mode, locked: false, pick: null, cache: null, claims: [], verdicts: {}, evidence: [], match: null,
    step, steps: [], t0: Date.now(), t1: 0, fillIn: [], done: null, field: null, unreachable: false, error: null, token: {},
    pickOpen: null,
  });
  // opts.locked: the person chose this address (a highlighted address on the page, a pick from a list); the
  // server keeps it. Unlocked (the generic button), the server may check another of the page's addresses.
  function startRun(a, opts) {
    stopRun();
    fiOpen = false;
    run = newRun(a, "server", "Connecting to the local checker…");
    run.locked = !!(opts && opts.locked);
    overlay.setSelected(a.text);
    rebuildMarks();
    render();
    const snapshot = {
      url: location.href, title: document.title, address_text: a.full,
      text: (document.body ? document.body.innerText : "").slice(0, 60000), jsonld: RC.jsonld(),
      address_candidates: RC.addressCandidates(addresses),
    };
    if (run.locked) snapshot.address_locked = true;
    const me = run;
    try {
      port = chrome.runtime.connect({ name: "rc-run" });
    } catch (e) { me.unreachable = true; render(); return; }
    const p = port;
    p.onMessage.addListener((m) => {
      if (run !== me) return;
      if (m.type === "event") handle(m.event);
      else if (m.type === "unreachable") { me.unreachable = true; render(); }
      else if (m.type === "end" && !me.done && !me.error) { me.error = "The checker stopped before finishing."; render(); }
    });
    p.onDisconnect.addListener(() => { if (port === p) port = null; void chrome.runtime.lastError; });
    p.postMessage({ type: "check", snapshot });
  }
  function startSample() {
    stopRun();
    const a = run.address;
    fiOpen = false;
    run = newRun(a, "sample", "Starting…");
    const me = run;
    render();
    RC_SAMPLE.events(a.full).forEach((ev, i) => setTimeout(() => { if (run === me && me.token) handle(ev); }, (i + 1) * RC_SAMPLE.delayMs));
  }
  function handle(ev) {
    if (!run) return;
    switch (ev.type) {
      case "step": {
        // One line per source, newest text wins; the most recently active line sits on top.
        run.step = ev.text;
        const k = AG[ev.agent] || "Working", s = run.steps.find((x) => x.k === k);
        if (s) { s.text = ev.text; s.t = ++stepSeq; } else run.steps.push({ k, text: ev.text, t: ++stepSeq });
        break;
      }
      case "run_started": {
        run.pick = ev.address_pick && typeof ev.address_pick === "object" ? ev.address_pick : null;
        run.cache = ev.cache || null;
        // the server may check another of the page's addresses than the one we sent
        if (ev.address && normA(ev.address) !== normA(run.address.full)) { run.address = addrFor(ev.address); overlay.setSelected(run.address.text); }
        break;
      }
      case "claims": { // several claims events add up, the first one per id wins (as the report's claim field reads them)
        const have = new Set(run.claims.map((c) => c.id));
        for (const c of ev.claims || []) if (c && c.id != null && !have.has(c.id)) { have.add(c.id); run.claims.push(c); }
        break;
      }
      case "match": run.match = ev.match || null; break;
      case "evidence": if (ev.evidence && typeof ev.evidence === "object") run.evidence.push(ev.evidence); break;
      case "verdict": run.verdicts[ev.verdict.claim_id] = ev.verdict; rebuildMarks(); break;
      case "fill_in": run.fillIn = Array.isArray(ev.facts) ? ev.facts : []; break;
      case "done": run.done = ev; run.field = fieldOf(ev); run.t1 = Date.now(); run.step = ""; break;
      case "error": run.error = ev.message || "Something went wrong."; break;
      default: return;
    }
    render();
  }

  // ---------- the claim field: one column per claim, one row per source ----------
  // Cell states: wait (nothing yet), none (this source has nothing on the claim), data (it has data, no call yet),
  // agree (it backs the listing), contra (it does not, or only nearly).
  function relevant(ev, c) {
    if (!ev || ev.status !== "ok") return false;
    const o = ev.observed || {};
    if (o.feature_check) return Array.isArray(o.claim_ids) && o.claim_ids.includes(c.id) && o.presence !== "skipped";
    if (c.label && ev.source === "scan" && !o.plan) return false; // a labelled claim ignores scans of no plan
    if (c.label && o.plan && String(o.plan).toLowerCase() !== String(c.label).toLowerCase()) return false;
    if (o[c.kind] != null && o[c.kind] !== "") return true;
    if (UNIT_KINDS.has(c.kind) && Array.isArray(o.floorplans) && o.floorplans.length) return true;
    if (c.kind === "stories" && o.height_m != null) return true;
    return false;
  }
  function ownReading(evs, c, src) {
    // true / false when this source's own number settles it on its face; null when only the judge can say.
    if (!COMPARABLE.has(c.kind) || c.scope === "unit" || src === "scan") return null;
    const want = num(c.value);
    if (!Number.isFinite(want)) return null;
    const vals = evs.map((e) => num((e.observed || {})[c.kind])).filter(Number.isFinite);
    if (!vals.length) return null;
    if (vals.every((v) => v === want)) return true;
    if (vals.every((v) => v !== want)) return false;
    return null;
  }
  // The finished run's cells come from the server (done.field: server/claim_field.py, the report's own rules), so the
  // drawer and the report show the same grid. The rules below only fill the grid while the run is going (and for
  // a sample run or an older checker that sends no field).
  const FIELD_STATE = { match: "agree", contra: "contra", close: "close", data: "data", none: "none" };
  function fieldOf(ev) {
    const f = ev && ev.field;
    if (!f || !Array.isArray(f.claims)) return null;
    const by = {}, order = [];
    for (const c of f.claims) if (c && c.id != null && c.cells) { by[c.id] = c; order.push(c.id); }
    return { by, order };
  }
  function fieldClaims(r) {
    // the server's column order when it sent one (claims it never saw keep their place at the end)
    if (!r.field) return r.claims;
    const pos = new Map(r.field.order.map((id, i) => [id, i]));
    return r.claims.map((c, i) => [c, pos.has(c.id) ? pos.get(c.id) : 1e6 + i]).sort((a, b) => a[1] - b[1]).map(([c]) => c);
  }
  function cellState(r, c, src) {
    const fc = r.field && r.field.by[c.id];
    if (fc) return FIELD_STATE[fc.cells[src]] || "none";
    const evs = r.evidence.filter((e) => e.source === src && relevant(e, c));
    const v = r.verdicts[c.id];
    if (!v) return evs.length ? "data" : r.done ? "none" : "wait";
    if (v.source === src && v.verdict !== "cant_check") return BAD.has(v.verdict) ? "contra" : "agree";
    if (!evs.length) return "none";
    const own = ownReading(evs, c, src);
    return own === true ? "agree" : own === false ? "contra" : "data";
  }
  function colGap(n) { return n > 70 ? 1 : n > 40 ? 2 : 3; }
  function fieldHTML(r, final) {
    const n = r.claims.length;
    const spread = Math.min(18, 520 / Math.max(1, n)); // whole stagger stays near half a second
    const cols = n ? fieldClaims(r).map((c, i) => {
      const v = r.verdicts[c.id];
      const tip = (c.quote || c.kind) + (v ? " — " + ({ contradicted: "not true", close: "close", confirmed: "true", cant_check: "couldn't check" }[v.verdict] || "") : "");
      return `<span class="col" data-id="${esc(c.id)}" title="${esc(tip)}" style="--d:${Math.round(i * spread)}ms">` +
        SOURCES.map((s, ri) => `<i class="cell" data-src="${s}" data-s="${cellState(r, c, s)}" style="--r:${ri}"></i>`).join("") + `</span>`;
    }).join("") : Array.from({ length: 18 }, (_, i) => `<span class="col ghost" style="--d:${i * 40}ms">${"<i class=\"cell\" data-s=\"wait\"></i>".repeat(4)}</span>`).join("");
    return `<div class="field${final ? " final" : ""}" id="field" role="img" aria-label="${esc(fieldLabel(r))}" style="--g:${colGap(n)}px;--hb:${n > 26 ? 1 : 1.5}px">
      <div class="rl" aria-hidden="true">${SOURCES.map((s) => `<span>${SRC[s]}</span>`).join("")}</div>
      <div class="cols" id="cols">${cols}</div></div>`;
  }
  function fieldLabel(r) {
    const n = r.claims.length, nv = Object.keys(r.verdicts).length;
    if (!n) return "Claim field, waiting for the listing's claims.";
    return `Claim field: ${n} claims, one column each; rows CoStar, Matterport, Aerial, County. ${nv} of ${n} judged. Black means a source backs the claim, blue means it does not.`;
  }
  function updateField(r) {
    const cols = $("cols");
    if (!cols) return;
    const have = cols.querySelectorAll(".col:not(.ghost)").length;
    if (have !== r.claims.length) { $("field").outerHTML = fieldHTML(r, false); return; }
    const colEls = cols.children;
    r.claims.forEach((c, i) => {
      const cells = colEls[i].children;
      SOURCES.forEach((s, ri) => { const st = cellState(r, c, s); if (cells[ri].dataset.s !== st) cells[ri].dataset.s = st; });
    });
    $("field").setAttribute("aria-label", fieldLabel(r));
  }

  // ---------- clock ----------
  const clock = (ms) => { const s = Math.max(0, Math.floor(ms / 1000)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
  const took = (ms) => { const s = Math.max(1, Math.round(ms / 1000)); return s < 60 ? s + " s" : Math.floor(s / 60) + " min " + (s % 60) + " s"; };
  setInterval(() => {
    if (!open || !run || run.done || run.error || run.unreachable) return;
    const el = $("elapsed");
    if (el) el.textContent = clock(Date.now() - run.t0);
  }, 1000);

  // ---------- render ----------
  let built = "";
  function phaseOf() {
    if (!run) return "idle";
    if (run.unreachable) return "unreachable";
    if (run.error) return "error";
    return run.done ? "result" : "running";
  }
  function render(force) {
    const p = phaseOf();
    $("ft").hidden = p !== "result";
    const key = p + ":" + (run ? run.seq : 0);
    if (!open && !force && p !== "running") { built = ""; return; } // rebuilt (and animated) when the drawer opens
    if (key !== built || force && p !== "result" && p !== "running") {
      built = key;
      body.scrollTop = 0;
      if (p === "idle") return renderIdle();
      if (p === "unreachable" || p === "error") return renderTrouble(p);
      if (p === "result") return renderResult();
      body.innerHTML = runningHTML(run);
    }
    if (p === "running") updateRunning(run);
  }

  function runningHTML(r) {
    return `<div class="run">
      <div class="clock"><span class="tm" id="elapsed" role="timer" aria-label="Time so far">${clock(Date.now() - r.t0)}</span>
        <span class="cm" id="claimsN"></span></div>
      ${fieldHTML(r, false)}
      <ol class="steps" id="steps" aria-label="What the check is doing now" aria-live="polite"></ol>
      <div class="chk" id="chk"></div>
      <p class="where" id="where">${whereText(r)}</p>
    </div>`;
  }
  function whereText(r) {
    return esc(r.address.full) + (r.mode === "sample" ? " · sample result" : "") + (r.cache === "off" ? ` · <span class="fresh">fresh run</span>` : "");
  }
  function updateRunning(r) {
    const n = r.claims.length;
    updateCheck(r);
    const wh = $("where");
    if (wh) { const t = whereText(r); if (wh.dataset.h !== t) { wh.dataset.h = t; wh.innerHTML = t; } }
    const cm = $("claimsN");
    if (cm) cm.textContent = n ? `${n} ${n === 1 ? "claim" : "claims"} found` : "Reading the listing";
    updateField(r);
    const st = $("steps");
    if (!st) return;
    const list = r.steps.length ? r.steps.slice().sort((a, b) => b.t - a.t).slice(0, 5) : [{ k: "Starting", text: r.step, t: 0 }];
    const top = list[0].k + "|" + list[0].t;
    const fresh = st.dataset.top !== top;
    st.dataset.top = top;
    st.innerHTML = list.map((s, i) => {
      const src = AG_SRC[s.k];
      const cnt = src ? r.claims.filter((c) => ["data", "agree", "contra", "close"].includes(cellState(r, c, src))).length : 0;
      return `<li class="${i === 0 ? "now" + (fresh ? " in" : "") : ""}"><span class="sd" aria-hidden="true"></span>
        <span class="sx"><span class="sk">${esc(s.k)}</span><span class="st">${esc(s.text)}</span></span>
        <span class="sc">${cnt ? cnt : ""}</span></li>`;
    }).join("");
  }

  function counts(r) {
    const c = r.done && r.done.counts;
    if (c && typeof c === "object") return { contradicted: c.contradicted || 0, close: c.close || 0, confirmed: c.confirmed || 0, cant_check: c.cant_check || 0 };
    const k = { contradicted: 0, close: 0, confirmed: 0, cant_check: 0 };
    for (const v of Object.values(r.verdicts)) if (v.verdict in k) k[v.verdict]++;
    return k;
  }
  function headlineHTML(r, k) {
    const bad = k.contradicted + k.close;
    if (bad > 0) {
      const one = bad === 1;
      return `<h1 class="hl" id="headline" data-n="${bad}"><span class="hw"><span class="hn">${bad}</span> ${one ? "thing" : "things"}</span><br><span class="hw ind">${one ? "doesn't" : "don't"} match.</span></h1>`;
    }
    const noMatch = r.match && r.match.status === "no_match";
    const text = k.confirmed > 0 ? "Everything we could check matches."
      : noMatch ? "We couldn't find this building." : "We couldn't check anything here.";
    return `<h1 class="hl calm" id="headline" data-n="0"><span class="hw">${text}</span></h1>`;
  }
  function listingWords(r, c, v) {
    // The page's own words when they are about this claim alone; else what was claimed, in plain words.
    const shared = r.claims.filter((x) => x.quote === c.quote).length > 1;
    if (c.quote && !shared && c.origin !== "metadata") return `“${c.quote}”`;
    return said(c) || v.claimed || c.quote || "";
  }
  function settledBy(r, c, v) {
    const want = BAD.has(v.verdict) ? ["contra", "close"] : ["agree"];
    const by = SOURCES.filter((s) => want.includes(cellState(r, c, s))).map((s) => SRC[s]);
    return (v.verdict === "close" ? "Close · " : "") + (by.join(", ") || SRC[v.source] || "");
  }
  function sizeClass(t) { const n = String(t).length; return n > 34 ? " s" : n > 18 ? " m" : ""; }
  function renderResult() {
    const r = run, k = counts(r);
    const bad = r.claims.map((c, i) => [c, r.verdicts[c.id], i]).filter(([, v]) => v && BAD.has(v.verdict))
      .sort((a, b) => ORDER[a[1].verdict] - ORDER[b[1].verdict] || a[2] - b[2]);
    // a verdict for a claim we never got (should not happen) still counts in the headline, not in the rows
    const meta = [
      r.mode === "sample" ? `<span>Sample result</span>` : "",
      r.cache === "off" ? `<span class="fresh" data-k="fresh">fresh run</span>` : "",
      k.confirmed ? `<span data-k="confirmed">${k.confirmed} true</span>` : "",
      k.close ? `<span data-k="close" class="b">${k.close} close</span>` : "",
      k.cant_check ? `<span data-k="cant_check">${k.cant_check} couldn't check</span>` : "",
      `<span data-k="time">${took((r.t1 || Date.now()) - r.t0)}</span>`,
    ].filter(Boolean).join(`<span class="sep" aria-hidden="true"> · </span>`);
    let h = `<div class="res">${headlineHTML(r, k)}<p class="meta" id="counts">${meta}</p><div class="chk" id="chk"></div>`;
    if (r.claims.length) h += fieldHTML(r, true);
    if (bad.length) {
      h += `<ul class="finds" id="finds" aria-label="What doesn't match">` + bad.slice(0, FIND_TOP).map(([c, v], i) => {
        const was = listingWords(r, c, v), is = v.found || "", pic = proofOf(r, c, v);
        return `<li><button class="find${pic ? " has-pic" : ""}" data-id="${esc(c.id)}" data-verdict="${v.verdict}" style="--i:${i}" aria-label="${esc(`Listing says ${v.claimed}. ${SRC[v.source] || "We"} found ${is}. Show it`)}">
          <span class="ftx"><span class="was"><span class="wt">${esc(was)}</span><i class="strike" aria-hidden="true"></i></span>
          <span class="is${sizeClass(is)}">${esc(is)}</span>
          <span class="by">${esc(settledBy(r, c, v))}</span></span>${pic ? `<span class="pic" data-img="${esc(pic)}" aria-hidden="true"></span>` : ""}</button></li>`;
      }).join("") + `</ul>`;
      if (bad.length > FIND_TOP) h += `<button class="morebtn" id="moreF">+${bad.length - FIND_TOP} more</button>`;
    }
    h += `<div class="fi" id="fi">${r.fillIn && r.fillIn.length ? fillInHTML(r.fillIn) : ""}</div></div>`;
    body.innerHTML = h;
    updateCheck(r);
    loadPics();
    fitHeadline();
    // footer: the walkthrough of the worst finding a 3D scan settled, and other addresses on this page
    const walk = r.mode === "server" && r.done.run_id ? bad.find(([, v]) => v.source === "scan") : null;
    $("ftl").innerHTML = (walk ? `<button class="lnk" id="btnWalk" data-id="${esc(walk[0].id)}">Walk through it in 3D</button>` : "") +
      (addresses.length > 1 ? `<button class="lnk" id="btnAnother">Check another address</button>` : "");
  }
  // ---------- proof pictures: small, lazy, fetched by the service worker (the page never loads from localhost) ----------
  const PROOF_RE = /^\/img\/[A-Za-z0-9_-]{1,64}$/;
  function proofOf(r, c, v) {
    const fc = r.field && r.field.by[c.id];
    const p = (fc && fc.image) || v.image;
    return typeof p === "string" && PROOF_RE.test(p) ? p : "";
  }
  function showPic(slot, url) {
    if (!slot.isConnected) return;
    if (!url) { const f = slot.closest(".find"); slot.remove(); if (f) f.classList.remove("has-pic"); return; } // missing: no gap left
    const im = document.createElement("img");
    im.alt = ""; im.decoding = "async";
    im.addEventListener("load", () => slot.classList.add("on"));
    im.addEventListener("error", () => showPic(slot, ""));
    im.src = url;
    slot.replaceChildren(im);
  }
  function fetchPic(slot) {
    const p = slot.dataset.img;
    if (imgCache.has(p)) { const u = imgCache.get(p); if (u !== null) showPic(slot, u); return; }
    imgCache.set(p, null);
    const settle = (u) => { imgCache.set(p, u); body.querySelectorAll(".pic[data-img]").forEach((s) => { if (s.dataset.img === p) showPic(s, u); }); };
    try {
      chrome.runtime.sendMessage({ type: "img", path: p }, (res) => { void chrome.runtime.lastError; settle((res && res.dataUrl) || ""); });
    } catch (e) { settle(""); }
  }
  let picIO = null;
  function loadPics() {
    const slots = body.querySelectorAll(".pic[data-img]");
    if (!slots.length) return;
    if (!picIO) picIO = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { picIO.unobserve(e.target); fetchPic(e.target); } }), { root: body, rootMargin: "120px" });
    slots.forEach((s) => picIO.observe(s));
  }

  // ---------- "Checking: <building> · <address> · <why>" and "Not this? Pick another" ----------
  const normA = (t) => String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  function addrFor(text) {
    const k = normA(text);
    return addresses.find((a) => normA(a.full) === k || normA(a.text) === k) || { text: String(text), full: String(text), ranges: [] };
  }
  function altsOf(r) {
    // the server's other candidates (address, and building name when known), else the page's other addresses
    const seen = new Set([normA(r.address.full)]), out = [];
    const add = (text, name) => { const k = normA(text); if (k && !seen.has(k)) { seen.add(k); out.push({ text: String(text), name: name || "" }); } };
    for (const a of (r.pick && r.pick.alternatives) || []) if (a && a.text) add(a.text, a.name);
    if (!out.length) for (const a of addresses) add(a.full, "");
    return out.slice(0, 5);
  }
  function checkHTML(r) {
    if (r.mode !== "server" || !r.match) return "";
    const m = r.match, pk = r.pick || {};
    const ok = m.status === "matched";
    const name = ok && m.name ? m.name : "";
    const addr = (ok && m.address) || pk.chosen || r.address.full;
    const low = typeof pk.confidence === "number" && pk.confidence < LOW_PICK;
    const alts = altsOf(r);
    const shown = alts.length > 0 && (r.pickOpen == null ? low && !r.locked : r.pickOpen); // opened for a guess, not for a pick
    // a guess keeps it short: the soft note replaces the reason (kept as the tooltip)
    const line = [name, addr, low ? "" : pk.why].filter(Boolean).map(esc).join(" · ");
    let h = `<p class="chkl${low ? " low" : ""}" id="chkLine"${low && pk.why ? ` title="${esc(pk.why)}"` : ""}><span class="chkw">Checking:</span> ${line}${low ? `. <span class="unsure">We're not sure this is the right building.</span>` : ""}</p>`;
    if (alts.length) {
      h += `<button class="lnk chkb" id="pickOther" aria-expanded="${shown}">Not this? Pick another</button>`;
      if (shown) h += `<ul class="alts" id="alts">` + alts.map((a) => `<li><button class="alt" data-t="${esc(a.text)}"><span class="aa">${esc(a.text)}</span>${a.name ? `<span class="an">${esc(a.name)}</span>` : ""}</button></li>`).join("") + `</ul>`;
    }
    return h;
  }
  function updateCheck(r) {
    const el = $("chk");
    if (!el) return;
    const h = checkHTML(r);
    if (el.dataset.h !== h) { el.dataset.h = h; el.innerHTML = h; }
  }

  function fitHeadline() {
    // Each headline line stays one line: shrink the type until the widest line fits (fallback fonts run wide).
    const hl = $("headline");
    if (!hl || hl.classList.contains("calm")) return;
    const avail = hl.clientWidth, widest = Math.max(...[...hl.querySelectorAll(".hw")].map((x) => x.offsetWidth));
    if (widest > avail) hl.style.fontSize = Math.floor(parseFloat(getComputedStyle(hl).fontSize) * avail / widest) + "px";
  }
  function fillInHTML(facts) {
    // Extra things the listing doesn't mention. Not a fact-check: neutral ink, no verdict colours.
    const n = facts.length;
    let h = `<button class="filine${fiOpen ? " open" : ""}" id="fiToggle" aria-expanded="${fiOpen}"><span class="fit">Not on the listing (${n})</span><span class="go">${CHEV}</span></button>`;
    if (!fiOpen) return h;
    h += `<ul class="facts" id="fiList">` + facts.slice(0, FI_TOP).map((f, i) => `<li class="fact" data-id="${esc(f.id)}" style="--i:${i}">
      <div class="fq"><span class="fl">${esc(f.label)}</span><span class="fs">${esc(SRC[f.source] || f.source || "")}</span></div>
      <div class="fv">${esc(f.value)}</div>${f.note ? `<div class="fn">${esc(f.note)}</div>` : ""}</li>`).join("") + `</ul>`;
    h += `<button class="lnk fiall" id="fiAll">See all in the full report</button>`;
    return h;
  }
  function said(c) {
    const n = num(c.value), s = (w) => (n === 1 ? w : w + "s");
    if (!Number.isFinite(n)) return String(c.value || c.quote || "");
    if (c.kind === "bedrooms") return n === 0 ? "Studio" : `${n} ${s("bedroom")}`;
    if (c.kind === "bathrooms") return `${n} ${s("bathroom")}`;
    if (c.kind === "area_sqft") return `${n.toLocaleString("en-US")} sq ft`;
    if (c.kind === "stories") return `${n} ${s("story").replace("storys", "stories")}`;
    if (c.kind === "year_built") return `Built in ${n}`;
    if (c.kind === "unit_count") return `${n.toLocaleString("en-US")} ${s("home")}`;
    return c.quote || String(c.value);
  }
  function renderTrouble(p) {
    const r = run;
    let h = `<div class="trouble">`;
    if (p === "unreachable") {
      h += `<p class="notice">Local checker is not running.</p>
        <div class="btnrow"><button class="btn secondary" id="useSample">Use sample result</button><button class="lnk" id="retry">Try again</button></div>`;
    } else {
      h += `<p class="notice">${esc(r.error)}</p><div class="btnrow"><button class="lnk" id="retry">Try again</button></div>`;
    }
    body.innerHTML = h + `<p class="where">${esc(r.address.full)}</p></div>`;
  }

  // Click on a finding: flash its words on the page; with no mark on the page, open the report at it.
  function showFinding(cid) {
    if (overlay.flash(cid)) return;
    openReport("#claim-" + encodeURIComponent(cid));
  }
  // Click on a mark on the page: point at the claim in the drawer.
  function pointAt(cid) {
    const sel = (cls) => body.querySelector(`${cls}[data-id="${String(cid).replace(/["\\]/g, "\\$&")}"]`);
    const el = sel(".find") || sel(".col");
    if (!el) return;
    el.classList.remove("ping"); void el.offsetWidth; el.classList.add("ping");
    if (el.classList.contains("find")) { el.focus({ preventScroll: true }); }
    el.scrollIntoView({ block: "nearest", behavior: REDUCED ? "auto" : "smooth" });
  }
  body.addEventListener("click", (e) => {
    const f = e.target.closest(".find");
    if (f) { showFinding(f.dataset.id); return; }
    if (e.target.closest("#moreF")) { openReport("#mismatch"); return; }
    if (e.target.closest("#fiToggle")) {
      fiOpen = !fiOpen;
      $("fi").innerHTML = fillInHTML(run.fillIn);
      $("fiToggle").focus({ preventScroll: true });
      if (fiOpen) $("fi").scrollIntoView({ block: "nearest", behavior: REDUCED ? "auto" : "smooth" });
      return;
    }
    if (e.target.closest("#fiAll")) { openReport("#extra"); return; }
    if (e.target.closest("#useSample")) { startSample(); return; }
    if (e.target.closest("#retry")) { startRun(run.address, { locked: run.locked }); return; }
    if (e.target.closest("#pickOther")) {
      run.pickOpen = $("pickOther").getAttribute("aria-expanded") !== "true";
      updateCheck(run);
      const b = $("pickOther");
      if (b) b.focus({ preventScroll: true });
      if (run.pickOpen && $("alts")) $("alts").scrollIntoView({ block: "nearest", behavior: REDUCED ? "auto" : "smooth" });
      return;
    }
    const alt = e.target.closest(".alt");
    if (alt) { startRun(addrFor(alt.dataset.t), { locked: true }); return; } // the person picked it: the server keeps it
    if (e.target.closest("#checkThis")) { startRun(addresses[0]); return; } // generic: the server may pick another address
    const pick = e.target.closest(".pick");
    if (pick) startRun(addresses[+pick.dataset.i], { locked: true });
  });
  function renderIdle() {
    if (!addresses.length) {
      body.innerHTML = `<div class="idle"><p class="lead">No street address on this page.</p></div>`;
      return;
    }
    body.innerHTML = `<div class="idle"><p class="lead">Is this listing what it says?</p><button class="btn primary chk1" id="checkThis">Check this listing</button><p class="hint">Or pick the address yourself, here or highlighted on the page.</p><ul class="picks">` +
      addresses.map((a, i) => `<li><button class="pick" data-i="${i}"><span class="pa">${esc(a.full)}</span><span class="go">${CHEV}</span></button></li>`).join("") + `</ul></div>`;
  }

  // ---------- rescan on DOM changes ----------
  // Wait for 500 ms of quiet, but never more than 2 s after the first change: pages that never stop
  // changing (tickers, carousels) would otherwise push the rescan back forever.
  let moT = 0, moFirst = 0;
  const QUIET_MS = 500, MAX_WAIT_MS = 2000;
  function rescanNow() { clearTimeout(moT); moT = 0; moFirst = 0; scan(); }
  new MutationObserver(() => {
    const now = Date.now();
    if (!moFirst) moFirst = now;
    clearTimeout(moT);
    moT = setTimeout(rescanNow, Math.max(0, Math.min(QUIET_MS, moFirst + MAX_WAIT_MS - now)));
  })
    .observe(document.body || document.documentElement, { childList: true, subtree: true, characterData: true });
  scan();

  function CSS() { return `
  :host{all:initial}
  *{box-sizing:border-box}
  .layer,.drawer,.tab,.pill,.tip{--serif:Newsreader,"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,"Times New Roman",serif;
    --ink:#111;--mute:#6b6b6b;--blue:#1a4be0;--line:#e6e6e6;--ease:cubic-bezier(.2,.8,.2,1);
    font-family:var(--serif);color:var(--ink);-webkit-font-smoothing:antialiased;letter-spacing:normal;text-transform:none;line-height:1.45;text-align:left;font-weight:400;font-style:normal}
  button{font:inherit;color:inherit;cursor:pointer;background:none;border:0;padding:0;margin:0;text-align:inherit}
  button:focus-visible,input:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
  [hidden]{display:none!important}
  h1,p,ul,ol{margin:0;padding:0}

  .tab{position:fixed;right:0;height:44px;display:flex;align-items:stretch;background:#fff;border:1px solid var(--line);border-right:0;border-radius:10px 0 0 10px;
    box-shadow:-2px 2px 10px rgba(0,0,0,.05);transition:transform .3s var(--ease),opacity .3s var(--ease);user-select:none}
  .tab.away{transform:translateX(110%);opacity:0;pointer-events:none}
  .tab.gone{display:none}
  .tab-face{width:44px;display:grid;place-items:center}
  .tab-handle{width:0;overflow:hidden;opacity:0;display:grid;grid-template-columns:repeat(2,3px);grid-auto-rows:3px;gap:3px;align-content:center;justify-content:center;
    cursor:grab;transition:width .18s ease,opacity .18s ease}
  .tab:hover .tab-handle,.tab.dragging .tab-handle{width:16px;opacity:1}
  .tab.dragging .tab-handle{cursor:grabbing}
  .tab-handle i{width:3px;height:3px;border-radius:50%;background:#b5b5b5}
  .tab-close{position:absolute;left:-8px;top:-8px;width:18px;height:18px;border-radius:50%;background:#fff;border:1px solid var(--line);color:var(--mute);
    display:grid;place-items:center;opacity:0;transition:opacity .15s ease}
  .tab:hover .tab-close,.tab-close:focus-visible{opacity:1}
  .tab-close:hover{color:var(--ink)}

  .drawer{position:fixed;top:0;right:0;bottom:0;width:${W}px;background:#fff;border-left:1px solid var(--line);box-shadow:-10px 0 30px rgba(0,0,0,.06);
    display:flex;flex-direction:column;font-size:17px;transform:translateX(100%);visibility:hidden;transition:transform .36s var(--ease),visibility 0s linear .36s}
  .drawer.open{transform:none;visibility:visible;transition:transform .36s var(--ease)}
  .hd{flex:none;display:flex;align-items:center;justify-content:space-between;padding:12px 12px 0 24px}
  .brand{font-size:17px;font-weight:500}
  .act{display:flex;align-items:center}
  .icon{width:44px;height:44px;display:grid;place-items:center;color:var(--ink)}
  .icon:hover{background:#eee}
  .menu{flex:none;padding:12px 24px 14px;border-bottom:1px solid var(--line)}
  .menu label{display:block;font-size:15px;color:var(--mute);margin-bottom:6px}
  .menu-row{display:flex;gap:8px}
  .menu input{flex:1;min-width:0;height:44px;border:1px solid #cfcfcf;padding:0 10px;font:15px var(--serif);color:var(--ink);background:#fff;border-radius:0}
  .menu input:focus{border-color:var(--ink);outline:none}
  .hint{font-size:15px;color:var(--mute);margin-top:6px}

  .body{flex:1;overflow-y:auto;overflow-x:hidden;padding:0 24px 24px;scrollbar-width:thin;scrollbar-color:#ddd transparent;display:flex;flex-direction:column}
  .run,.res,.trouble,.idle{flex:1 0 auto;display:flex;flex-direction:column;min-width:0}

  /* running */
  .clock{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;padding-top:20px}
  .tm{font-size:96px;font-weight:300;line-height:.82;letter-spacing:-.05em;font-variant-numeric:tabular-nums}
  .cm{font-size:15px;color:var(--mute);padding-bottom:6px;text-align:right}
  .run .field{margin-top:28px}
  .field{display:grid;grid-template-columns:max-content minmax(0,1fr);column-gap:10px;--ch:26px}
  .rl{display:grid;grid-template-rows:repeat(4,var(--ch));row-gap:var(--g);font-size:12px;color:var(--mute);line-height:var(--ch);text-align:right}
  .cols{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,22px);column-gap:var(--g);min-width:0}
  .col{display:grid;grid-template-rows:repeat(4,var(--ch));row-gap:var(--g);min-width:0;animation:rc-col .45s var(--ease) var(--d) backwards;transform-origin:bottom}
  .col.ghost .cell{animation:rc-breathe 1.6s ease-in-out var(--d) infinite alternate}
  .col.ping{animation:rc-ping 1.2s ease-out}
  .cell{display:block;background:#f4f4f4;transition:background-color .45s ease,box-shadow .45s ease;transition-delay:calc(var(--r,0) * 80ms)}
  .cell[data-s="none"]{background:#ebebeb}
  .cell[data-s="data"]{background:#fff;box-shadow:inset 0 0 0 var(--hb,1.5px) var(--ink)}
  .cell[data-s="agree"]{background:var(--ink)}
  .cell[data-s="contra"],.cell[data-s="close"]{background:var(--blue)}
  .field.final .cell{animation:rc-cell .5s var(--ease) calc(var(--d) + var(--r) * 45ms + 260ms) backwards}
  @keyframes rc-col{from{transform:scaleY(.2);opacity:0}}
  @keyframes rc-cell{from{background:#f4f4f4;box-shadow:none}}
  @keyframes rc-breathe{from{opacity:1}to{opacity:.35}}
  @keyframes rc-ping{0%,40%{outline:2px solid var(--blue);outline-offset:2px}100%{outline:2px solid transparent;outline-offset:6px}}
  .steps{list-style:none;margin-top:28px;display:flex;flex-direction:column;gap:14px}
  .steps li{display:grid;grid-template-columns:16px minmax(0,1fr) auto;column-gap:14px;align-items:baseline;color:var(--mute)}
  .sd{width:9px;height:9px;border-radius:50%;border:1.5px solid #9a9a9a;justify-self:center;transform:translateY(-1px)}
  .sx{display:flex;flex-direction:column;min-width:0}
  .sk{font-size:17px}
  .st{font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sc{font-size:15px;font-variant-numeric:tabular-nums}
  .steps li.now{color:var(--ink)}
  .steps li.now .sk{font-weight:500}
  .steps li.now .sd{background:var(--ink);border-color:var(--ink);box-shadow:0 0 0 0 rgba(17,17,17,.35);animation:rc-pulse 1.6s ease-out infinite}
  .steps li.now .st{animation:rc-breathe 1.2s ease-in-out infinite alternate}
  .steps li.in{animation:rc-in .4s var(--ease)}
  @keyframes rc-pulse{to{box-shadow:0 0 0 7px rgba(17,17,17,0)}}
  @keyframes rc-in{from{transform:translateY(-8px);opacity:0}}
  .chk{font-size:14px;line-height:1.4;color:var(--mute)}
  .run .chk{margin-top:24px}
  .res .chk{margin-top:8px}
  .chk:empty{display:none}
  .chkl{overflow-wrap:anywhere}
  .chkw,.chkl .unsure{color:var(--ink)}
  .chkb{min-height:36px;font-size:14px;color:var(--mute)}
  .alts{list-style:none;margin:2px 0 4px}
  .alts li{border-top:1px solid var(--line)}
  .alt{width:100%;min-height:44px;display:flex;flex-direction:column;justify-content:center;padding:6px 0;font-size:15px;color:var(--ink)}
  .alt:hover{color:var(--blue)}
  .alt .an{font-size:13px;color:var(--mute)}
  .fresh{color:var(--mute);font-size:13px;border:1px solid var(--line);padding:0 5px}
  .where{margin-top:auto;padding-top:28px;font-size:15px;color:var(--mute);overflow-wrap:anywhere}

  /* result */
  .hl{margin-top:16px;font-weight:300;font-size:60px;line-height:.98;letter-spacing:-.045em;overflow-wrap:break-word}
  .hl:not(.calm) .hw{white-space:nowrap}
  .hl .ind{padding-left:.66em}
  .hl.calm{font-size:46px;line-height:.98;letter-spacing:-.035em}
  .hw{display:inline-block;animation:rc-rise .7s var(--ease) backwards}
  .hw.ind{animation-delay:.08s}
  .hn{display:inline-block;animation:rc-settle .9s cubic-bezier(.2,1.5,.35,1) .1s backwards}
  @keyframes rc-rise{from{transform:translateY(.25em);opacity:0}}
  @keyframes rc-settle{0%{transform:translateY(-.5em);opacity:0}60%{opacity:1}}
  .meta{margin-top:12px;font-size:15px;color:var(--mute);animation:rc-fade .5s ease .2s backwards}
  .meta .b{color:var(--blue)}
  .res .field{margin-top:24px}
  .finds{list-style:none;margin:18px -12px 0}
  .find{display:flex;align-items:center;gap:12px;width:100%;min-height:44px;padding:10px 12px;transition:background-color .18s ease;
    animation:rc-rise .55s var(--ease) calc(.45s + var(--i) * .12s) backwards}
  .find:hover{background:#f4f4f4}
  .ftx{display:flex;flex-direction:column;align-items:flex-start;gap:1px;flex:1;min-width:0}
  .pic{flex:none;width:72px;height:54px;background:#f4f4f4;overflow:hidden}
  .pic img{display:block;width:100%;height:100%;object-fit:cover;opacity:0;transition:opacity .3s ease}
  .pic.on img{opacity:1}
  .find.ping{animation:rc-ping 1.2s ease-out}
  .was{position:relative;display:inline-block;max-width:100%;font-size:19px;line-height:1.25;color:#858585}
  .wt{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .strike{position:absolute;left:-2px;right:-2px;top:54%;height:2px;background:var(--blue);transform-origin:left center;
    animation:rc-strike .7s cubic-bezier(.7,0,.2,1) calc(.75s + var(--i) * .14s) backwards}
  @keyframes rc-strike{from{transform:scaleX(0)}}
  .is{max-width:100%;font-size:30px;font-weight:300;line-height:1.08;letter-spacing:-.025em;color:var(--blue);overflow-wrap:anywhere;
    display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .is.m{font-size:25px}
  .is.s{font-size:21px;line-height:1.15}
  .by{font-size:13px;color:var(--mute);margin-top:2px}
  .morebtn{min-height:44px;font-size:15px;text-decoration:underline;text-underline-offset:4px;align-self:flex-start;margin-top:2px}
  .morebtn:hover,.lnk:hover{color:var(--blue)}
  .fi{margin-top:14px}
  .filine{width:100%;min-height:44px;display:flex;gap:12px;align-items:center;border-top:1px solid var(--line);font-size:15px;color:var(--ink)}
  .filine .fit{flex:1}
  .filine .go{display:grid;color:#9a9a9a;transition:transform .25s var(--ease)}
  .filine.open .go{transform:rotate(90deg)}
  .filine:hover .go{color:var(--ink)}
  .facts{list-style:none}
  .fact{padding:10px 0;border-top:1px solid #f0f0f0;animation:rc-rise .4s var(--ease) calc(var(--i) * 50ms) backwards}
  .fq{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
  .fl{font-size:15px;color:var(--mute);min-width:0}
  .fs{flex:none;font-size:13px;color:#9a9a9a}
  .fv{font-size:17px;color:var(--ink);overflow-wrap:anywhere}
  .fn{font-size:13px;color:#8a8a8a;margin-top:1px}
  .fiall{margin-top:2px}
  @keyframes rc-fade{from{opacity:0}}

  /* idle + trouble */
  .lead{margin-top:20px;font-size:32px;font-weight:300;line-height:1.05;letter-spacing:-.025em}
  .idle .hint{margin-top:14px}
  .chk1{margin-top:18px}
  .picks{list-style:none;margin-top:18px}
  .picks li{border-top:1px solid var(--line)}
  .pick{width:100%;min-height:52px;display:flex;align-items:center;gap:10px;padding:10px 0;font-size:17px}
  .pick .pa{flex:1;min-width:0;overflow-wrap:anywhere}
  .pick .go{color:#9a9a9a;display:grid}
  .pick:hover .go{color:var(--ink)}
  .notice{margin-top:20px;font-size:32px;font-weight:300;line-height:1.05;letter-spacing:-.025em}
  .btnrow{display:flex;gap:20px;align-items:center;margin-top:20px}

  .ft{flex:none;display:flex;flex-direction:column;gap:4px;padding:12px 24px 18px;background:#fff}
  .btn{min-height:44px;padding:0 16px;font-size:15px;white-space:nowrap;transition:background-color .18s ease,transform .18s ease}
  .btn:active{transform:translateY(1px)}
  .primary{width:100%;min-height:52px;background:var(--ink);color:#fff;font-size:17px;text-align:center}
  .primary:hover{background:#333}
  .secondary{background:#fff;border:1px solid var(--ink);color:var(--ink)}
  .secondary:hover{background:#f4f4f4}
  .ftl{display:flex;justify-content:center;flex-wrap:wrap;column-gap:24px}
  .ftl:empty{display:none}
  .lnk{min-height:44px;font-size:15px;text-decoration:underline;text-underline-offset:4px;color:var(--ink)}
  @media (prefers-reduced-motion: reduce){*,*::before,*::after{transition:none!important;animation:none!important}}
  `; }
})();
