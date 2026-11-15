// Realty Check: edge tab, drawer and run flow. Everything lives in ONE shadow root on <div id="rc-host">.
// The drawer has two states: Running (clock, claim field, step list) and Result (headline, field, top findings).
(() => {
  if (window.top !== window || document.getElementById("rc-host")) return;
  // Never on the checker's own pages (its report, 3D tour and data routes): the button would cover the report.
  const OWN_HOST = /^(127\.0\.0\.1|localhost|\[::1\])$/i, OWN_PATH = /^\/(report|tour|artifacts|runs|field|img|health)(\/|$)/;
  if ((OWN_HOST.test(location.hostname) && OWN_PATH.test(location.pathname)) || location.protocol === "chrome-extension:") return;
  const RC = globalThis.RC;
  const W = 420;
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const INK = "#111111", BLUE = "#1a4be0";
  // Page marks: one blue for every mismatch (contradicted or close), ink for "true", grey for "couldn't check".
  const VC = { contradicted: BLUE, close: BLUE, confirmed: INK, cant_check: "#b5b5b5" };
  const SOURCES = ["record", "scan", "aerial", "county"];
  const SRC = { record: "CoStar", scan: "Matterport", aerial: "Vexcel", county: "County" };
  const AG = { orchestrator: "Listing", claims: "Listing", matcher: "CoStar", inside: "Matterport", outside: "Vexcel", paper: "County", judge: "Judging" };
  const AG_SRC = { CoStar: "record", Matterport: "scan", Vexcel: "aerial", County: "county" };
  // the live worker list, in this order always (rows never move while a check runs)
  const WORKERS = ["Listing", "CoStar", "Matterport", "Vexcel", "County", "Judging"];
  // the "at work" mark: a 3x3 grid of squares a wave travels across (CSS only; static under reduced motion)
  const LOADER = `<span class="sd" aria-hidden="true">${"<i></i>".repeat(9)}</span>`;
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
    <div class="cc" id="rcCard" role="dialog" hidden></div>
  </aside>`;
  const $ = (id) => root.getElementById(id);
  const tab = $("tab"), drawer = $("drawer"), body = $("body");

  // ---------- state ----------
  let addresses = [], flat = null;
  let run = null; // {seq, address, mode, locked, pick, cache, claims, verdicts, evidence, match, steps, t0, t1, fillIn, done, field, unreachable, error, token, pickOpen}
  let open = false, seq = 0, stepSeq = 0;
  const ORDER = { contradicted: 0, close: 1, confirmed: 2, cant_check: 9 };
  let okOpen = false, cantOpen = false; // "N confirmed" / "N couldn't check" expanded
  const FACT_TOP = 5, Q_TOP = 5; // the report's own caps: five facts, five questions
  const LOW_PICK = 0.5; // below this the address pick is a guess: say so and open the picker

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
    ccClose(true);
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
    okOpen = cantOpen = false;
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
    p.onDisconnect.addListener(() => {
      const mine = port === p; // false when we closed it ourselves (stopRun)
      if (mine) port = null;
      void chrome.runtime.lastError;
      // the link to the checker dropped mid-run: say so, never keep counting
      if (mine && run === me && !me.done && !me.error && !me.unreachable) { me.error = "The checker stopped before finishing."; render(); }
    });
    p.postMessage({ type: "check", snapshot });
  }
  function startSample() {
    stopRun();
    const a = run.address;
    okOpen = cantOpen = false;
    run = newRun(a, "sample", "Starting…");
    const me = run;
    render();
    RC_SAMPLE.events(a.full).forEach((ev, i) => setTimeout(() => { if (run === me && me.token) handle(ev); }, (i + 1) * RC_SAMPLE.delayMs));
  }
  function handle(ev) {
    if (!run) return;
    switch (ev.type) {
      case "step": {
        // One line per worker, newest text wins; rows keep a fixed order (WORKERS). Every worker runs at the same
        // time, so each row is working (on) from its first step until the judge starts (the sources have reported)
        // or the run ends; a later step of its own wakes it again.
        run.step = ev.text;
        const k = AG[ev.agent] || "Working", s = run.steps.find((x) => x.k === k);
        if (k === "Judging") for (const x of run.steps) if (x.k !== k) x.on = false;
        if (s) { s.text = ev.text; s.t = ++stepSeq; s.on = true; } else run.steps.push({ k, text: ev.text, t: ++stepSeq, on: true });
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
      case "verdict": run.verdicts[ev.verdict.claim_id] = cleanVerdict(ev.verdict); rebuildMarks(); break;
      case "fill_in": run.fillIn = Array.isArray(ev.facts) ? ev.facts : []; break;
      case "done": run.done = ev; run.field = fieldOf(ev); run.t1 = Date.now(); run.step = ""; for (const x of run.steps) x.on = false; break;
      case "error": run.error = ev.message || "Something went wrong."; for (const x of run.steps) x.on = false; break;
      default: return;
    }
    render();
  }

  // Older runs tagged where a value came from inside `found` ("630 sq ft (scan title)"): never show such a tag.
  const PROVENANCE_TAG = /\s*\((?:scan title|record|county|aerial|scan|property data)\)\s*$/i;
  function cleanVerdict(v) {
    if (!v || typeof v.found !== "string" || !PROVENANCE_TAG.test(v.found)) return v;
    return { ...v, found: v.found.replace(PROVENANCE_TAG, "") };
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
    if (!COMPARABLE.has(c.kind) || c.scope === "unit" || src === "scan" || c.value_max != null) return null;
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
  // Blue cells (contra, close) carry the server's shade (high | medium | low: how sure the run is), drawn as the blue's
  // strength; no shade sent: contra full, close faint. An unknown state draws as none.
  const FIELD_STATE = { match: "agree", contra: "contra", close: "close", data: "data", none: "none" };
  const SHADES = new Set(["high", "medium", "low"]);
  function fieldOf(ev) {
    const f = ev && ev.field;
    if (!f || !Array.isArray(f.claims)) return null;
    const by = {}, order = [];
    for (const c of f.claims) if (c && c.id != null && c.cells && typeof c.cells === "object") { by[c.id] = c; order.push(c.id); }
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
    if (fc) return (Object.prototype.hasOwnProperty.call(FIELD_STATE, fc.cells[src]) && FIELD_STATE[fc.cells[src]]) || "none";
    const evs = r.evidence.filter((e) => e.source === src && relevant(e, c));
    const v = r.verdicts[c.id];
    if (!v) return evs.length ? "data" : r.done ? "none" : "wait";
    if (v.source === src && v.verdict !== "cant_check") return BAD.has(v.verdict) ? "contra" : "agree";
    if (!evs.length) return "none";
    const own = ownReading(evs, c, src);
    return own === true ? "agree" : own === false ? "contra" : "data";
  }
  // The blue's strength for a blue cell: the server's shade, else the verdict's own (shadeOf), else contra full and
  // close faint. "" for a cell that is not blue.
  function cellShade(r, c, src, st) {
    if (st !== "contra" && st !== "close") return "";
    const fc = r.field && r.field.by[c.id];
    const sent = fc && fc.shade && typeof fc.shade === "object" ? fc.shade[src] : null;
    if (SHADES.has(sent)) return sent;
    const v = r.verdicts[c.id];
    if (!fc && v && BAD.has(v.verdict)) return shadeOf(v);
    return st === "contra" ? "high" : "low";
  }
  function colGap(n) { return n > 70 ? 1 : n > 40 ? 2 : 3; }
  function fieldHTML(r, final) {
    const n = r.claims.length;
    const spread = Math.min(18, 520 / Math.max(1, n)); // whole stagger stays near half a second
    const cols = n ? fieldClaims(r).map((c, i) => {
      const v = r.verdicts[c.id];
      const tip = (c.quote || c.kind) + (v ? " — " + ({ contradicted: "doesn't match", close: "doesn't match", confirmed: "true", cant_check: "couldn't check" }[v.verdict] || "") : "");
      return `<span class="col" data-id="${esc(c.id)}" title="${esc(tip)}" style="--d:${Math.round(i * spread)}ms">` +
        SOURCES.map((s, ri) => { const st = cellState(r, c, s), sh = cellShade(r, c, s, st); return `<i class="cell" data-src="${s}" data-s="${st}"${sh ? ` data-sh="${sh}"` : ""} style="--r:${ri}"></i>`; }).join("") + `</span>`;
    }).join("") : Array.from({ length: 18 }, (_, i) => `<span class="col ghost" style="--d:${i * 40}ms">${"<i class=\"cell\" data-s=\"wait\"></i>".repeat(4)}</span>`).join("");
    return `<div class="field${final ? " final" : ""}" id="field" role="img" aria-label="${esc(fieldLabel(r))}" style="--g:${colGap(n)}px;--hb:${n > 26 ? 1 : 1.5}px">
      <div class="rl" aria-hidden="true">${SOURCES.map((s) => `<span>${SRC[s]}</span>`).join("")}</div>
      <div class="cols" id="cols">${cols}</div></div>`;
  }
  function fieldLabel(r) {
    const n = r.claims.length, nv = Object.keys(r.verdicts).length;
    if (!n) return "Claim field, waiting for the listing's claims.";
    return `Claim field: ${n} claims, one column each; rows CoStar, Matterport, Vexcel, County. ${nv} of ${n} judged. Black means a source backs the claim, blue means it does not; a paler blue, we are less sure.`;
  }
  function updateField(r) {
    const cols = $("cols");
    if (!cols) return;
    const have = cols.querySelectorAll(".col:not(.ghost)").length;
    if (have !== r.claims.length) { $("field").outerHTML = fieldHTML(r, false); return; }
    const colEls = cols.children;
    r.claims.forEach((c, i) => {
      const cells = colEls[i].children;
      SOURCES.forEach((s, ri) => {
        const st = cellState(r, c, s), sh = cellShade(r, c, s, st), el = cells[ri];
        if (el.dataset.s !== st) el.dataset.s = st;
        if ((el.dataset.sh || "") !== sh) { if (sh) el.dataset.sh = sh; else delete el.dataset.sh; }
      });
    });
    $("field").setAttribute("aria-label", fieldLabel(r));
  }

  // ---------- clock ----------
  const clock = (ms) => { const s = Math.max(0, Math.floor(ms / 1000)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
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
      <ul class="finds live" id="liveFinds" aria-label="What doesn't match so far" aria-live="polite" hidden></ul>
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
    updateLive(r);
    const st = $("steps");
    if (!st) return;
    const ks = WORKERS.concat(r.steps.map((x) => x.k).filter((k) => !WORKERS.includes(k)));
    if (st.dataset.ks !== ks.join("|")) { // rows are built once (and when an unknown worker turns up), then updated in place
      st.dataset.ks = ks.join("|");
      st.innerHTML = ks.map((k, i) => `<li data-k="${esc(k)}" data-s="idle" style="--ph:${(-0.31 * i).toFixed(2)}s">${LOADER}` +
        `<span class="sx"><span class="sk">${esc(k)}</span><span class="st"></span></span><span class="sc"></span></li>`).join("");
    }
    for (const li of st.children) {
      const k = li.dataset.k;
      let s = r.steps.find((x) => x.k === k);
      if (!s && k === "Listing" && !r.steps.length && r.step) s = { text: r.step, on: true }; // before the first step
      const state = !s ? "idle" : s.on ? "on" : "done";
      if (li.dataset.s !== state) li.dataset.s = state;
      const text = s ? s.text : "Waiting to start", te = li.querySelector(".st");
      if (te.textContent !== text) { te.textContent = text; te.title = text; if (s) bump(te, "chg"); }
      const src = AG_SRC[k];
      const cnt = src ? r.claims.filter((c) => ["data", "agree", "contra", "close"].includes(cellState(r, c, src))).length
        : k === "Listing" ? r.claims.length : k === "Judging" ? Object.keys(r.verdicts).length : 0;
      const ce = li.querySelector(".sc"), prev = Number(ce.dataset.n || 0);
      if (prev !== cnt) { ce.dataset.n = cnt; ce.textContent = cnt ? cnt : ""; if (cnt > prev) bump(ce, "tick"); }
    }
  }
  // restart a one-shot CSS animation class
  function bump(el, cls) { el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls); }

  function counts(r) {
    const c = r.done && r.done.counts;
    if (c && typeof c === "object") return { contradicted: c.contradicted || 0, close: c.close || 0, confirmed: c.confirmed || 0, cant_check: c.cant_check || 0 };
    const k = { contradicted: 0, close: 0, confirmed: 0, cant_check: 0 };
    for (const v of Object.values(r.verdicts)) if (v.verdict in k) k[v.verdict]++;
    return k;
  }
  // The server's headline (done.top: server/report_a.py's counts, ranking and words), so the drawer and the report
  // always name the same thing; older checkers and the sample send none, and the drawer counts for itself.
  function topOf(r) {
    const t = r.done && r.done.top;
    return t && typeof t.headline === "string" && Array.isArray(t.lines) && t.lines.length === 2 ? t : null;
  }
  function headlineHTML(r, k) {
    const t = topOf(r);
    if (t && t.calm) return `<h1 class="hl calm" id="headline" data-n="0"><span class="hw">${esc(t.headline)}</span></h1>`;
    if (t) {
      const m = /^(\d+)\s+(.*)$/.exec(t.lines[0]);
      const l1 = m ? `<span class="hn">${esc(m[1])}</span> ${esc(m[2])}` : esc(t.lines[0]);
      return `<h1 class="hl" id="headline" data-n="${Number(t.hard) || 0}"><span class="hw">${l1}</span><br><span class="hw ind">${esc(t.lines[1])}</span></h1>`;
    }
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
  function creditKeys(r, c, v) {
    // The finished run's credit comes from the server (done.report rows / done.field[].credit: server/report_a.py's
    // rule): only the sources whose own figure is the found value, the deciding one first. A source that differs
    // from the listing with yet another figure is never credited. Without it (a running or sample run) the same
    // rule runs here.
    const fc = r.field && r.field.by[c.id];
    const sent = fc && Array.isArray(fc.credit) ? fc.credit.filter((s) => SRC[s]) : null;
    const keys = sent && sent.length ? sent : creditOf(r, c, v);
    return keys.length ? keys : SRC[v.source] ? [v.source] : [];
  }
  // The other sources that spoke to this claim (the judge's list, else any cell with something to say), for the
  // "also checked" citations: never credited with the found value (server/report_a.py's _also_keys).
  function alsoKeys(r, c, v, keys) {
    const conf = v && v.confidence && typeof v.confidence === "object" ? v.confidence : {};
    const spoke = new Set((Array.isArray(conf.sources) ? conf.sources : []).map((x) => x && x.source).filter((k) => SRC[k]));
    for (const k of SOURCES) if (["agree", "contra", "close", "data"].includes(cellState(r, c, k))) spoke.add(k);
    return SOURCES.filter((k) => spoke.has(k) && !keys.includes(k));
  }
  // server/report_a.py's _credit/_supports: the decider (else the verdict's source), then each other source in the
  // confidence block whose own `says` is the found value (same first number, else the same words).
  function firstNum(t) { const m = String(t == null ? "" : t).replace(/,/g, "").match(/-?\d+(?:\.\d+)?/); return m ? Number(m[0]) : null; }
  function supports(says, found) {
    const a = normA(says), b = normA(found);
    if (!a || !b) return false;
    const nb = firstNum(found);
    return nb !== null ? firstNum(says) === nb : a === b;
  }
  function creditOf(r, c, v) {
    const conf = (v && v.confidence && typeof v.confidence === "object") ? v.confidence : {};
    const keys = [];
    const lead = [conf.decided_by, v.source].find((k) => SRC[k]);
    if (lead) keys.push(lead);
    if (Array.isArray(conf.sources) && conf.sources.length) {
      for (const s of conf.sources) if (s && SRC[s.source] && !keys.includes(s.source) && s.agrees_with_listing !== true && supports(s.says, v.found)) keys.push(s.source);
      return keys;
    }
    // no confidence block: the sources whose own cell disagrees with the listing, as before
    const want = BAD.has(v.verdict) ? ["contra", "close"] : ["agree"];
    for (const s of SOURCES) if (!keys.includes(s) && want.includes(cellState(r, c, s))) keys.push(s);
    return keys;
  }
  function sizeClass(t) { const n = String(t).length; return n > 34 ? " s" : n > 18 ? " m" : ""; }
  // ---------- how sure we are: only on "not true" and "close" verdicts that carry a confidence block ----------
  const BANDS = { high: ["High confidence", 3], medium: ["Medium confidence", 2], low: ["Low confidence", 1] };
  // server/report_a.py's _shade / _strength / _tag: every contradicted or close claim is a mismatch; how sure the run
  // is only sets how strong the blue is (high full, medium mid, low faint) and the order (most certain first).
  const SHADE_SCORE = { high: 85, medium: 60, low: 30 };
  function scoreOf(v) {
    const c = v && v.confidence;
    if (!c || typeof c !== "object" || typeof c.score !== "number" || !Number.isFinite(c.score) || c.score < 0 || c.score > 100) return null;
    return Math.round(c.score);
  }
  function bandOf(v) {
    const b = v && v.confidence && typeof v.confidence === "object" ? String(v.confidence.band || "").toLowerCase() : "";
    return SHADES.has(b) ? b : "";
  }
  function shadeOf(v) {
    const b = bandOf(v), s = scoreOf(v);
    if (b) return b;
    if (s != null) return s >= 75 ? "high" : s >= 45 ? "medium" : "low";
    return v && v.verdict === "contradicted" ? "high" : "low";
  }
  function strengthOf(v) { const s = scoreOf(v); return s != null ? s : SHADE_SCORE[shadeOf(v)]; }
  // server/report_a.py's _score_html: the score as a serif numeral with a small "/ 100 · high"; no score: the level
  // word alone at that size (never a made-up number); neither: nothing.
  function scoreTag(v) {
    const s = scoreOf(v), b = bandOf(v);
    if (s != null) return { n: String(s), u: "/ 100" + (b ? " · " + b : ""), label: `Confidence ${s} of 100${b ? ", " + b : ""}` };
    return b ? { w: cap(b), label: `${cap(b)} confidence` } : null;
  }
  function scoreHTML(t) {
    if (!t) return "";
    return `<span class="ftag" role="img" aria-label="${esc(t.label)}">` +
      (t.n ? `<span class="scn">${esc(t.n)}</span><span class="scu">${esc(t.u)}</span>` : `<span class="scw">${esc(t.w)}</span>`) + `</span>`;
  }
  function confOf(v) {
    const c = v && BAD.has(v.verdict) && v.confidence;
    if (!c || typeof c !== "object" || !BANDS[c.band]) return null;
    const score = Number.isFinite(Number(c.score)) && c.score !== null && c.score !== "" ? Math.round(Number(c.score)) : null;
    const srcs = (Array.isArray(c.sources) ? c.sources : []).filter((s) => s && SRC[s.source] && s.says != null && String(s.says).trim());
    return { band: c.band, score, why: typeof c.why === "string" ? c.why.trim() : "", by: SRC[c.decided_by] ? c.decided_by : null, srcs };
  }
  function confHTML(v, i) {
    const c = confOf(v);
    if (!c) return "";
    const [word, lit] = BANDS[c.band];
    // the decider first, then the others in the order the judge gave them
    const srcs = c.srcs.slice().sort((a, b) => (b.source === c.by) - (a.source === c.by));
    const rows = srcs.map((s) => `<li><span class="cn">${esc(SRC[s.source])}</span><span class="cv">${esc(s.says)}</span>` +
      (s.source === c.by ? `<span class="ct dec">decides</span>` : s.agrees_with_listing === true ? `<span class="ct">matches the listing</span>` : "") + `</li>`).join("");
    const by = c.by && !srcs.some((s) => s.source === c.by) ? `<p class="cfd">Decided by ${esc(SRC[c.by])}</p>` : "";
    const more = c.why || rows || by;
    const bars = `<span class="bars" aria-hidden="true">${[1, 2, 3].map((n) => `<i${n <= lit ? ` class="on"` : ""}></i>`).join("")}</span>`;
    const label = `${word}${c.score != null ? `, ${c.score} of 100` : ""}`;
    // the judge's 0-100 score next to the word ("82 · high confidence"); no score sent: the word only, never a guess
    // the score itself stands at the row's right (scoreHTML); this only opens why
    const inner = `${bars}<span class="cft">${c.score != null ? "Why this score" : word}</span>`;
    if (!more) return `<div class="cf" data-band="${c.band}"><span class="cfb" role="img" aria-label="${esc(label)}">${inner}</span></div>`;
    return `<div class="cf" data-band="${c.band}"><button class="cfb" aria-expanded="false" aria-controls="cfx${i}" aria-label="${esc(label + ". Why")}"${c.score != null ? ` title="Score ${c.score} of 100"` : ""}>${inner}<span class="go">${CHEV}</span></button>
      <div class="cfx" id="cfx${i}" hidden>${c.why ? `<p class="cfw">${esc(c.why)}</p>` : ""}${by}${rows ? `<ul class="cfl">${rows}</ul>` : ""}</div></div>`;
  }
  // ---------- the result: every mismatch, what the listing leaves out, questions, and the rest, as text ----------
  // The finished run's words come from the server (done.report: server/report_a.py's own helpers), so the drawer
  // says what the report says; a sample run or an older checker sends none, and the rules below stand in.
  const KIND_LABEL = { year_built: "Year built", stories: "Floors", unit_count: "Homes", area_sqft: "Size", bedrooms: "Bedrooms", bathrooms: "Bathrooms" };
  const SOFT_KINDS = new Set(["amenity", "parking", "laundry", "view", "other"]); // photo checks: never struck
  const UNKIND_RE = /\b(fake|faked|lie|lies|lied|lying|scam\w*|fraud\w*)\b/i;
  const FACT_BANNED = /parcel|building class|\bclass\b|lot size|\blot\b|rating|owner|manag|contact|phone|e-?mail|\bagent\b|leed/i;
  const MANAGED_RE = /\s*[;,]\s*(?:managed|operated|owned)\s+by\b.*$/i;
  const cap = (t) => { t = String(t || ""); return t ? t[0].toUpperCase() + t.slice(1) : t; };
  function kindText(t, max) {
    t = String(t == null ? "" : t).replace(/\s+/g, " ").trim();
    return !t || t.length > (max || 160) || UNKIND_RE.test(t) || t.includes("<") ? "" : t;
  }
  function firstSentence(t) {
    // "its plan Plan F 2 Bedroom" -> "its Plan F 2 Bedroom" (server/report_a.py's _tidy)
    const parts = String(t || "").trim().replace(/\b[Pp]lan\s+(?=Plan\b)/g, "").split(/(?<=[.!?])\s+(?=[A-Z])/).map((x) => x.trim()).filter(Boolean);
    if (parts.length > 1 && /^listing says/i.test(parts[0])) parts.shift();
    return parts[0] || "";
  }
  function repOf(r) {
    const p = r.done && r.done.report;
    return p && typeof p === "object" && Array.isArray(p.rows) ? p : null;
  }
  function kindLabel(c) { return [KIND_LABEL[c.kind] || cap(String(c.kind || "").replace(/_/g, " ")) || "Claim", c.label].filter(Boolean).join(" · "); }
  // Every mismatch (contradicted or close), in the report's order: the run's own pick (done.harness.top_mismatch) on
  // top, then the most certain first. No cap: the list scrolls inside the drawer.
  function badRows(r) {
    const rep = repOf(r);
    const srv = new Map(rep ? rep.rows.filter((x) => x && x.id != null).map((x) => [String(x.id), x]) : []);
    const t = topOf(r);
    const order = rep ? rep.rows.map((x) => x && x.id) : (t && Array.isArray(t.order) ? t.order : []);
    const rank = new Map(order.map((id, i) => [id, i]));
    const at = (c) => (rank.has(c.id) ? rank.get(c.id) : 1e6);
    // server/report_a.py's _ranked_off: strictly by score, highest first; no score after, a contradicted one before
    // a close one, then the server's (else the page's) order; an older run's saved order never overrides the score.
    // The run's own pick never moves a row.
    const sc = (v) => { const x = scoreOf(v); return x == null ? -1 : x; };
    const bad = r.claims.map((c, i) => [c, r.verdicts[c.id], i]).filter(([, v]) => v && BAD.has(v.verdict))
      .sort((a, b) => sc(b[1]) - sc(a[1]) || ORDER[a[1].verdict] - ORDER[b[1].verdict] || at(a[0]) - at(b[0]) || a[2] - b[2]);
    return bad.map(([c, v]) => {
      const s = srv.get(String(c.id));
      const hard = v.verdict === "contradicted";
      const sent = s && Array.isArray(s.credit) ? s.credit.filter((k) => SRC[k]) : [];
      const keys = sent.length ? sent : creditKeys(r, c, v);
      const also = s && Array.isArray(s.also) ? s.also.filter((k) => SRC[k] && !keys.includes(k)) : alsoKeys(r, c, v, keys);
      const src = { keys, also };
      const shade = s && SHADES.has(s.shade) ? s.shade : shadeOf(v);
      const tag = scoreTag(v);
      // the report's source preview cards ({} on an older run: the chips stay plain)
      const pv = s && s.previews && typeof s.previews === "object" && !Array.isArray(s.previews) ? s.previews : {};
      src.pv = pv;
      if (s) {
        return { c, v, id: c.id, label: String(s.label || kindLabel(c)), said: s.quoted ? `“${s.said}”` : String(s.said || ""),
          strike: true, found: String(s.found || v.found || ""), why: kindText(s.why), src, shade, tag,
          walk: typeof s.walk === "string" && /^\/tour\//.test(s.walk) ? s.walk : "" };
      }
      return { c, v, id: c.id, label: kindLabel(c), said: listingWords(r, c, v), strike: true,
        found: !hard && SOFT_KINDS.has(c.kind) ? "Not seen in the photos" : cap(String(v.found || "")),
        why: kindText(firstSentence(v.reason)), src, shade, tag, walk: "" };
    });
  }
  function findHTML(row, i, links, fresh) {
    const { v } = row;
    // struck through only when it doesn't match; a close one keeps the listing's words plain
    const was = row.strike ? `<span class="was"><s class="wt">${esc(row.said)}</s></span>` : `<span class="was"><span class="wt">${esc(row.said)}</span></span>`;
    const lk = links ? `<span class="fln"><button class="lnk lproof" data-id="${esc(row.id)}">Proof</button>` +
      (row.walk ? `<button class="lnk l3d" data-walk="${esc(row.walk)}">See it in 3D</button>` : "") + `</span>` : "";
    return `<li class="fr${fresh ? " in" : ""}" data-id="${esc(row.id)}" data-verdict="${esc(v.verdict)}" data-shade="${esc(row.shade)}" style="--i:${i}">` +
      `<button class="find" data-id="${esc(row.id)}" data-verdict="${esc(v.verdict)}" aria-label="${esc(`${row.label}. Listing says ${row.said}. Found ${row.found}. Show it`)}">` +
      `<span class="fkr"><span class="fk">${esc(row.label)}</span>${scoreHTML(row.tag)}</span>${was}<span class="is${sizeClass(row.found)}">${esc(row.found)}</span></button>` +
      (row.why ? `<p class="fy">${esc(row.why)}</p>` : "") +
      (row.src.keys.length ? `<p class="by">${srcHTML(row, links)}</p>` : "") + (lk ? `<p class="by bl">${lk}</p>` : "") + confHTML(v, i) + `</li>`;
  }
  // Citations, as the report shows them: "Source" then each source that backs the found value (the deciding one
  // first, emphasised), then "also checked" and the ones that spoke but differ. With a checker run each one links to
  // its evidence in the artifact library (<server>/artifacts/<run_id>#a-<claim>-<source>), in a new tab; a sample
  // stays text.
  const SLUG = { record: "costar", scan: "matterport", aerial: "vexcel", county: "county" };
  function artifactPath(id, key) {
    const rid = run && run.mode === "server" && run.done && run.done.run_id;
    if (!rid || !/^[A-Za-z0-9_-]{1,80}$/.test(String(rid))) return "";
    return `/artifacts/${encodeURIComponent(rid)}#a-${encodeURIComponent(id)}-${SLUG[key]}`;
  }
  function absUrl(path) {
    try { if (run && run.done && /^https?:/.test(run.done.report_url || "")) return new URL(path, run.done.report_url).href; } catch (err) { /* keep the path */ }
    return path;
  }
  function citeHTML(row, k, lead, links) {
    const href = links ? artifactPath(row.id, k) : "";
    const cls = `cite${lead ? " dec" : ""}`;
    const p = links && row.src.pv && row.src.pv[k];
    if (p && typeof p === "object") { // a preview card on hover, focus or tap (wired in wireCites)
      const at = `data-cc="${esc(k)}" data-row="${esc(row.id)}" aria-controls="rcCard" aria-expanded="false" aria-haspopup="dialog"`;
      return `<span class="cw">` + (href
        ? `<a class="${cls} srcl" ${at} role="button" href="${esc(absUrl(href))}" data-href="${esc(href)}" target="_blank" rel="noopener">${esc(SRC[k])}<i aria-hidden="true">↗</i></a>`
        : `<button type="button" class="${cls}" ${at}>${esc(SRC[k])}</button>`) + `</span>`;
    }
    return href
      ? `<a class="${cls} srcl" href="${esc(absUrl(href))}" data-href="${esc(href)}" target="_blank" rel="noopener">${esc(SRC[k])}<i aria-hidden="true">↗</i></a>`
      : `<span class="${cls}">${esc(SRC[k])}</span>`;
  }
  function srcHTML(row, links) {
    const { keys, also } = row.src;
    if (!keys.length) return "";
    return `<span class="citl">${keys.length > 1 ? "Sources" : "Source"}</span>` + keys.map((k, i) => citeHTML(row, k, i === 0, links)).join("") +
      (also.length ? `<span class="citl">also checked</span>` + also.map((k) => citeHTML(row, k, false, links)).join("") : "");
  }
  // Source preview cards (server/report_a.py's citation cards, in the drawer): one card, filled with textContent from
  // the row's done.report previews, opens on hover (90 ms), keyboard focus or a tap; it stays inside the drawer's
  // visible part, flips above the chip when there is no room below. Esc, x or a click outside closes it.
  let ccRows = new Map(), ccBtn = null, ccIn = 0, ccOut = 0, ccPtr = "mouse", ccPinned = false, ccQuiet = false;
  const card = $("rcCard");
  function ccBack(o) { if (!o || !o.isConnected) return; ccQuiet = true; try { o.focus({ preventScroll: true }); } catch (e) { /* gone */ } ccQuiet = false; }
  function ccFill(b) {
    const p = (ccRows.get(b.dataset.row) || {})[b.dataset.cc];
    if (!p || typeof p !== "object") return false;
    const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
    const role = ["decided", "agrees", "differs", "context"].includes(p.role) ? p.role : "context";
    const name = String(p.source || SRC[b.dataset.cc] || ""), tag = String(p.tag || "");
    card.replaceChildren();
    card.className = `cc r-${role}`;
    card.setAttribute("aria-label", [name, tag].filter(Boolean).join(", "));
    const x = mk("button", "cc-x", "\u00d7"); x.type = "button"; x.setAttribute("aria-label", "Close");
    const hd = mk("span", "cc-h"); hd.append(mk("b", "", name)); if (tag) hd.append(mk("em", "", tag));
    card.append(x, hd);
    if (p.value) card.append(mk("span", "cc-v", String(p.value)));
    if (p.read) card.append(mk("span", "cc-read", String(p.read)));
    const where = [p.where, p.date].filter(Boolean).map(String).join(" \u00b7 ");
    if (where) card.append(mk("span", "cc-w", where));
    const href = typeof p.href === "string" && /^\/artifacts\//.test(p.href) && run && run.mode === "server" ? p.href : "";
    if (href) {
      const a = mk("a", "cc-go", "Open in artifact library "); a.href = absUrl(href); a.dataset.href = href; a.target = "_blank"; a.rel = "noopener";
      const i = mk("i", "", "\u2197"); i.setAttribute("aria-hidden", "true"); a.append(i);
      card.append(a);
    }
    return true;
  }
  function ccPlace() {
    if (!ccBtn) return;
    const dr = drawer.getBoundingClientRect(), r = ccBtn.getBoundingClientRect(), m = 12;
    const vw = document.documentElement.clientWidth || innerWidth, vh = innerHeight;
    const L = Math.max(dr.left, 0) + m, R = Math.min(dr.right, vw) - m, T = Math.max(dr.top, 0) + m, B = Math.min(dr.bottom, vh) - m;
    const w = Math.max(0, Math.min(320, R - L));
    card.style.width = w + "px";
    const h = card.offsetHeight, x = Math.min(Math.max(L, r.left), R - w);
    const below = r.bottom + 8, above = r.top - 8 - h;
    const side = below + h <= B || above < T ? "bottom" : "top";
    const y = Math.max(T, Math.min(side === "bottom" ? below : above, B - h));
    card.style.left = Math.round(x - dr.left) + "px"; card.style.top = Math.round(y - dr.top) + "px";
    card.dataset.side = side;
  }
  function ccOpen(b, pin) {
    clearTimeout(ccOut); clearTimeout(ccIn);
    if (ccBtn === b) { ccPinned = ccPinned || !!pin; return; }
    ccClose(true);
    if (!b.isConnected || !ccFill(b)) return;
    card.hidden = false; ccBtn = b; ccPinned = !!pin; ccPlace(); void card.offsetWidth; card.classList.add("on");
    b.setAttribute("aria-expanded", "true");
  }
  function ccClose(now) {
    clearTimeout(ccIn); clearTimeout(ccOut);
    if (!ccBtn) return;
    const b = ccBtn; ccBtn = null; ccPinned = false;
    b.setAttribute("aria-expanded", "false"); card.classList.remove("on");
    if (now || REDUCED) { card.hidden = true; return; }
    setTimeout(() => { if (!card.classList.contains("on")) card.hidden = true; }, 160);
  }
  function ccLater() { clearTimeout(ccOut); if (!ccPinned) ccOut = setTimeout(() => ccClose(), 180); }
  function ccFocusIn() { const go = card.querySelector(".cc-go") || card.querySelector(".cc-x"); if (go) go.focus({ preventScroll: true }); }
  function wireCites(scope) {
    scope.querySelectorAll("[data-cc]").forEach((b) => {
      b.addEventListener("pointerdown", (e) => { ccPtr = e.pointerType || "mouse"; });
      b.addEventListener("mouseenter", () => { if (ccPtr === "touch") return; clearTimeout(ccOut); clearTimeout(ccIn); ccIn = setTimeout(() => ccOpen(b, false), ccBtn ? 0 : 90); });
      b.addEventListener("mouseleave", () => { clearTimeout(ccIn); if (ccBtn === b) ccLater(); });
      b.addEventListener("focus", () => { if (ccQuiet) return; let fv = true; try { fv = b.matches(":focus-visible"); } catch (e) { /* old engine */ } if (fv) ccOpen(b, false); });
      b.addEventListener("blur", () => setTimeout(() => { if (ccBtn === b && !ccPinned && !card.contains(root.activeElement)) ccClose(); }, 0));
      b.addEventListener("keydown", (e) => { if (e.key === " " || e.key === "ArrowDown") { e.preventDefault(); ccOpen(b, true); if (ccBtn === b) ccFocusIn(); } });
      b.addEventListener("click", (e) => {
        const touch = ccPtr === "touch"; ccPtr = "mouse";
        if (b.tagName === "A" && !touch && ccBtn === b && !ccPinned && e.detail) return; // a mouse click on a previewed chip opens the source
        e.preventDefault(); e.stopPropagation();
        if (ccBtn === b && ccPinned) { ccClose(); return; }
        ccOpen(b, true);
        if (e.detail === 0 && ccBtn === b) ccFocusIn();
      });
    });
  }
  card.addEventListener("mouseenter", () => clearTimeout(ccOut));
  card.addEventListener("mouseleave", () => ccLater());
  card.addEventListener("click", (e) => {
    if (e.target.closest(".cc-x")) { const o = ccBtn; ccClose(); ccBack(o); return; }
    const go = e.target.closest(".cc-go");
    if (go) { // opened by the service worker, as the chips are (the page's own CSP never sees it)
      e.preventDefault();
      if (run && run.done && run.mode === "server") send({ type: "openReport", report_url: absUrl(go.dataset.href) });
    }
  });
  // capture, so the drawer's own Esc (close the drawer) never fires while a card is open
  root.addEventListener("keydown", (e) => { if (e.key === "Escape" && ccBtn) { e.preventDefault(); e.stopPropagation(); const o = ccBtn; ccClose(); ccBack(o); } }, true);
  root.addEventListener("focusin", (e) => { if (ccBtn && !card.contains(e.target) && e.target !== ccBtn) ccClose(); });
  document.addEventListener("pointerdown", (e) => {
    if (!ccBtn) return;
    const path = e.composedPath();
    if (!path.includes(card) && !path.includes(ccBtn)) ccClose();
  }, true);
  addEventListener("resize", () => { if (ccBtn) ccPlace(); }, { passive: true });
  body.addEventListener("scroll", () => { if (ccBtn) requestAnimationFrame(ccPlace); }, { passive: true });
  // "Beyond the listing": the report's top five (done.report.facts, else done.harness.top_facts, else the fill_in
  // list in renter order), text only; never a manager, owner, parcel, class or lot size, and no "; managed by" clause.
  function factsOf(r) {
    const rep = repOf(r), hz = r.done && r.done.harness;
    const raw = rep && Array.isArray(rep.facts)
      ? rep.facts.map((f) => ({ id: "", label: f.label, value: f.value, why: f.why, src: (f.sources || []).join(" + ") }))
      : hz && Array.isArray(hz.top_facts) && hz.top_facts.length
        ? hz.top_facts.map((f) => ({ id: (f.fact_ids || [])[0] || "", label: f.label, value: f.value, why: f.why,
          src: (Array.isArray(f.sources) ? f.sources : [f.source]).map((x) => SRC[x] || x).filter(Boolean).join(" + ") }))
        : fiOrder(r.fillIn || []).map((f) => ({ id: f.id, label: f.label, value: f.value, why: f.note, src: SRC[f.source] || f.source || "" }));
    const out = [];
    for (const f of raw) {
      if (!f || f.label == null || f.value == null) continue;
      const label = String(f.label).replace(/\s+/g, " ").trim(), value = String(f.value).replace(MANAGED_RE, "").replace(/\s+/g, " ").trim();
      if (!label || !value || FACT_BANNED.test(label + " " + (f.id || "")) || /\b(managed|owned) by\b/i.test(value)) continue;
      if (UNKIND_RE.test(label + " " + value) || out.some((o) => o.label === label)) continue;
      out.push({ id: f.id || "", label, value, why: kindText(f.why), src: String(f.src || "") });
      if (out.length >= FACT_TOP) break;
    }
    return out;
  }
  // One line per fact. A value that never says what it counts ("18 since 2026-01-01 within 100 m") leads with its
  // label; one that already carries the label's words ("32 of 317 homes available") stands alone.
  function factLine(f) {
    const stems = (t) => String(t).toLowerCase().match(/[a-z]{4,}/g) || [];
    const val = stems(f.value).map((w) => w.slice(0, 5));
    return stems(f.label).some((w) => val.includes(w.slice(0, 5))) ? f.value : `${f.label}: ${f.value}`;
  }
  function factsHTML(facts) {
    if (!facts.length) return "";
    return `<section class="sec" id="fi" aria-labelledby="fiH"><h2 class="sl" id="fiH">Beyond the listing</h2><ul class="facts" id="fiList">` +
      facts.map((f, i) => `<li class="fact"${f.id ? ` data-id="${esc(f.id)}"` : ""} style="--i:${i}"` +
        ` title="${esc(f.label)}" aria-label="${esc(f.label + ": " + f.value)}"><div class="fv">${esc(factLine(f))}</div></li>`).join("") +
      `</ul><button class="lnk fiall" id="fiAll">More in the full report</button></section>`;
  }
  // "Ask before you tour": the report's drafted questions (done.report.questions); without them, the same template
  // the report uses (server/report_a.py _question), for the mismatches and then what nobody could check.
  const ASK_FROM = { scan: "the 3D scan shows", county: "county records say", record: "the CoStar building record says" };
  function questionsOf(r, rows) {
    const rep = repOf(r);
    const most = Math.max(Q_TOP, rows.length); // every mismatch gets its question, then the couldn't-checks up to five
    if (rep && Array.isArray(rep.questions)) return rep.questions.map((q) => kindText(q, 400)).filter(Boolean).slice(0, most);
    const qs = [];
    const cant = r.claims.map((c) => [c, r.verdicts[c.id]]).filter(([, v]) => v && v.verdict === "cant_check");
    for (const [c, v] of rows.map((x) => [x.c, x.v]).concat(cant)) {
      const said = listingWords(r, c, v), plan = c.label ? ` for ${c.label}` : "";
      const found = String(v.found || "").replace(/\s*\([^)]*\)\s*$/, "").trim();
      let q;
      if (BAD.has(v.verdict) && !SOFT_KINDS.has(c.kind) && found) {
        const who = ASK_FROM[v.source] || (c.kind === "stories" ? "a roof height measurement suggests" : "Vexcel aerial photos suggest");
        q = v.verdict === "close" ? `The listing says ${said}${plan}; ${who} ${found}. Which number is exact?`
          : `The listing says ${said}${plan}, but ${who} ${found}. Which is right?`;
      } else q = SOFT_KINDS.has(c.kind) ? `Can you show me ${said}${plan} when I tour?` : `Can you confirm ${said}${plan} in writing?`;
      q = kindText(q, 400);
      if (BAD.has(v.verdict)) { if (q && !qs.includes(q)) qs.push(q); continue; }
      if (qs.length >= Q_TOP) break;
      if (q && !qs.includes(q)) qs.push(q);
    }
    return qs;
  }
  function questionsHTML(qs) {
    if (!qs.length) return "";
    return `<section class="sec" id="ask" aria-labelledby="askH"><h2 class="sl" id="askH">Ask before you tour</h2><ol class="qs" id="qList">` +
      qs.map((q, i) => `<li class="q"><span class="qn">${i + 1}</span><span class="qt">${esc(q)}</span><button class="lnk qcopy" data-text="${esc(q)}" aria-label="Copy question ${i + 1}">Copy</button></li>`).join("") +
      `</ol>${qs.length > 1 ? `<button class="lnk qall" id="qAll" data-text="${esc(qs.join("\n"))}">Copy all ${qs.length}</button>` : ""}</section>`;
  }
  // "N confirmed" and "N couldn't check": collapsed lines that open to plain one-line rows.
  function checkedOf(r, which) {
    const rep = repOf(r), key = which === "ok" ? "ok" : "cant";
    if (rep && Array.isArray(rep[key])) return rep[key].filter((x) => x && x.text).map((x) => ({ label: String(x.label || ""), text: String(x.text), why: kindText(x.why) }));
    const want = which === "ok" ? "confirmed" : "cant_check";
    return r.claims.map((c) => [c, r.verdicts[c.id]]).filter(([, v]) => v && v.verdict === want)
      .map(([c, v]) => ({ label: KIND_LABEL[c.kind] || "", text: listingWords(r, c, v), why: which === "ok" ? "" : kindText(firstSentence(v.reason)) }));
  }
  function checkedHTML(r) {
    const block = (id, list, isOpen, word) => !list.length ? "" :
      `<button class="filine${isOpen ? " open" : ""}" id="${id}Toggle" aria-expanded="${isOpen}"><span class="fit">${list.length} ${word}</span><span class="go">${CHEV}</span></button>` +
      (isOpen ? `<ul class="cks" id="${id}List">` + list.map((x) => `<li class="ckr">${x.label ? `<span class="ckl">${esc(x.label)}</span>` : ""}<span class="ckt">${esc(x.text)}</span>${x.why ? `<span class="ckw">${esc(x.why)}</span>` : ""}</li>`).join("") + `</ul>` : "");
    const h = block("ok", checkedOf(r, "ok"), okOpen, "confirmed") + block("cant", checkedOf(r, "cant"), cantOpen, "couldn't check");
    return h ? `<section class="sec ck" id="checked">${h}</section>` : `<span id="checked"></span>`;
  }
  // ---------- which home the run checked: this unit, this home, or only the building ----------
  function whoHTML(r) {
    if (!r.done || (r.match && r.match.status === "no_match")) return "";
    const hz = (r.done.harness && typeof r.done.harness === "object") ? r.done.harness : {};
    const u = hz.unit != null ? String(hz.unit).trim() : "";
    const what = u ? `${/^\d/.test(u) ? "Unit " : ""}${u} (this home)` : hz.single_unit === true ? "this home" : "the building";
    const plan = r.evidence.some((e) => e.source === "scan" && e.status === "ok" && (e.observed || {}).scan_of === "plan");
    return `<p class="who" id="who"><span class="whw">Checked:</span> ${esc(what)}${plan ? `<span class="whp">3D scan is of the same floor plan</span>` : ""}</p>`;
  }
  function renderResult() {
    const r = run, k = counts(r);
    const t = topOf(r), rows = badRows(r);
    ccClose(true);
    ccRows = new Map(rows.map((x) => [String(x.id), x.src.pv || {}]));
    let h = `<div class="res">${whoHTML(r)}${headlineHTML(r, k)}${t && t.line ? `<p class="topl" id="topLine">${esc(t.line)}</p>` : ""}<div class="chk" id="chk"></div>`;
    if (r.claims.length) h += fieldHTML(r, true);
    if (rows.length) h += `<section class="sec" id="mm" aria-labelledby="mmH"><h2 class="sl" id="mmH">Mismatch</h2><ul class="finds" id="finds">` +
      rows.map((row, i) => findHTML(row, i, true, true)).join("") + `</ul></section>`;
    h += factsHTML(factsOf(r)) + questionsHTML(questionsOf(r, rows)) + checkedHTML(r) + `</div>`;
    body.innerHTML = h;
    wireCites(body);
    updateCheck(r);
    fitHeadline();
    // footer: the walkthrough of the worst finding a 3D scan settled, and other addresses on this page
    const walk = r.mode === "server" && r.done.run_id ? rows.find((x) => x.v.source === "scan") : null;
    $("ftl").innerHTML = (walk ? `<button class="lnk" id="btnWalk" data-id="${esc(walk.id)}">Walk through it in 3D</button>` : "") +
      (addresses.length > 1 ? `<button class="lnk" id="btnAnother">Check another address</button>` : "");
  }
  // While the run goes: each mismatch appears as its verdict arrives (no links until the report exists).
  const liveSeen = new Set();
  function updateLive(r) {
    const el = $("liveFinds");
    if (!el) return;
    const rows = badRows(r);
    const key = rows.map((x) => x.id + ":" + x.v.verdict).join("|");
    if (el.dataset.k === key) return;
    el.dataset.k = key;
    el.innerHTML = rows.map((row, i) => { const fresh = !liveSeen.has(r.seq + ":" + row.id); liveSeen.add(r.seq + ":" + row.id); return findHTML(row, i, false, fresh); }).join("");
    el.hidden = !rows.length;
  }
  function copyText(text, btn) {
    const done = () => {
      if (!btn.dataset.label) btn.dataset.label = btn.textContent;
      btn.textContent = "Copied";
      clearTimeout(btn._t); btn._t = setTimeout(() => { btn.textContent = btn.dataset.label; }, 1400);
    };
    const fallback = () => {
      const ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", ""); ta.setAttribute("style", "position:fixed;left:-9999px;top:0;opacity:0");
      root.appendChild(ta); ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { /* not allowed */ }
      ta.remove();
      if (ok) done();
    };
    try { navigator.clipboard.writeText(text).then(done, fallback); } catch (e) { fallback(); }
  }
  // ---------- a guess says so; "Not this? Pick another" ----------
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
    const pk = r.pick || {};
    const low = typeof pk.confidence === "number" && pk.confidence < LOW_PICK;
    const alts = altsOf(r);
    const shown = alts.length > 0 && (r.pickOpen == null ? low && !r.locked : r.pickOpen); // opened for a guess, not for a pick
    // only a guess gets a line (the reason kept as the tooltip)
    let h = low ? `<p class="chkl low" id="chkLine"${pk.why ? ` title="${esc(pk.why)}"` : ""}>We're not sure this is the right building.</p>` : "";
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
  // Only a few fill_in rows show in the drawer (when no top facts were sent), so the most useful to a renter lead: sizes of homes first (at most two),
  // then year built or last renovated (one), then what the aerial survey saw on the lot; then the rest, as sent.
  const FI_TIERS = [[/^(unit-|scan-size-|size-\d+bed$|plan-(?!count$|more$))/, 2], [/^(year-built|renovated)$/, 1], [/^aerial-features$/, 1]];
  function fiOrder(facts) {
    const head = FI_TIERS.map(() => []), rest = [];
    for (const f of facts) {
      const id = String((f && f.id) || "");
      const t = FI_TIERS.findIndex(([re]) => re.test(id));
      if (t >= 0 && head[t].length < FI_TIERS[t][1]) head[t].push(f); else rest.push(f);
    }
    return head.flat().concat(rest);
  }
  function said(c) {
    const n = num(c.value), s = (w) => (n === 1 ? w : w + "s");
    if (!Number.isFinite(n)) return String(c.value || c.quote || "");
    if (c.value_max != null && Number.isFinite(num(c.value_max))) { // a range for the whole building
      const m = num(c.value_max), lo = c.kind === "bedrooms" && n === 0 ? "Studio" : n.toLocaleString("en-US");
      const unit = { bedrooms: m === 1 ? "bedroom" : "bedrooms", bathrooms: m === 1 ? "bathroom" : "bathrooms", area_sqft: "sq ft" }[c.kind] || "";
      return `${lo} to ${m.toLocaleString("en-US")} ${unit}`.trim();
    }
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
    const cb = e.target.closest("button.cfb");
    if (cb) {
      const x = root.getElementById(cb.getAttribute("aria-controls")), on = cb.getAttribute("aria-expanded") !== "true";
      cb.setAttribute("aria-expanded", String(on));
      if (x) { x.hidden = !on; if (on) x.scrollIntoView({ block: "nearest", behavior: REDUCED ? "auto" : "smooth" }); }
      return;
    }
    const f = e.target.closest(".find");
    if (f) { showFinding(f.dataset.id); return; }
    const pf = e.target.closest(".lproof");
    if (pf) { openReport("#claim-" + encodeURIComponent(pf.dataset.id)); return; }
    const w3 = e.target.closest(".l3d");
    if (w3 && run && run.done && run.mode === "server") {
      let u = w3.dataset.walk;
      try { if (/^https?:/.test(run.done.report_url || "")) u = new URL(u, run.done.report_url).href; } catch (err) { /* a path: the service worker adds the server */ }
      send({ type: "openReport", report_url: u });
      return;
    }
    const sl = e.target.closest(".srcl");
    if (sl) e.preventDefault(); // opened in a new tab by the service worker (the page's own CSP never sees it)
    if (sl && run && run.done && run.mode === "server") {
      let u = sl.dataset.href;
      try { if (/^https?:/.test(run.done.report_url || "")) u = new URL(u, run.done.report_url).href; } catch (err) { /* a path: the service worker adds the server */ }
      send({ type: "openReport", report_url: u });
      return;
    }
    const cp = e.target.closest(".qcopy, #qAll");
    if (cp) { copyText(cp.dataset.text || "", cp); return; }
    const tg = e.target.closest("#okToggle, #cantToggle");
    if (tg) {
      if (tg.id === "okToggle") okOpen = !okOpen; else cantOpen = !cantOpen;
      const sec = $("checked");
      if (sec) sec.outerHTML = checkedHTML(run);
      const b = $(tg.id);
      if (b) { b.focus({ preventScroll: true }); b.scrollIntoView({ block: "nearest", behavior: REDUCED ? "auto" : "smooth" }); }
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
  h1,h2,p,ul,ol{margin:0;padding:0}

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
  .cell[data-s="data"]{background:#ebebeb}
  /* while the run is on: a source that has brought something for a claim shows at once, before the judge calls it */
  .run .cell[data-s="data"]{background:#9a9a9a;animation:rc-got .45s var(--ease) backwards}
  @keyframes rc-got{from{background:#ebebeb;transform:scaleY(.4)}}
  .cell[data-s="agree"]{background:var(--ink)}
  .cell[data-s="contra"],.cell[data-s="close"]{background:var(--blue)}
  .cell[data-s="close"]:not([data-sh]),.cell[data-sh="low"]{background:#bccbf7}
  .cell[data-sh="medium"]{background:#6a8bee}
  .field.final .cell{animation:rc-cell .5s var(--ease) calc(var(--d) + var(--r) * 45ms + 260ms) backwards}
  @keyframes rc-col{from{transform:scaleY(.2);opacity:0}}
  @keyframes rc-cell{from{background:#f4f4f4;box-shadow:none}}
  @keyframes rc-breathe{from{opacity:1}to{opacity:.35}}
  @keyframes rc-ping{0%,40%{outline:2px solid var(--blue);outline-offset:2px}100%{outline:2px solid transparent;outline-offset:6px}}
  .steps{list-style:none;margin-top:28px;display:flex;flex-direction:column;gap:14px}
  .steps li{display:grid;grid-template-columns:18px minmax(0,1fr) auto;column-gap:14px;align-items:start;color:var(--mute)}
  /* the worker's mark: idle a faint hollow square, working a 3x3 wave (ink, its crest the one blue), done a muted check.
     Wave and line shimmer ported by hand from beui.dev "Agent loading states" (Agent Progress grid, Thinking Shimmer,
     Reasoning Text "swap"; github.com/starc007/ui-components): timings kept, colours and corners ours. */
  .sd{position:relative;width:16px;height:16px;margin-top:4px;display:grid;grid-template-columns:repeat(3,4px);grid-auto-rows:4px;gap:2px;justify-self:center}
  .sd i{display:block;background:var(--ink);opacity:0}
  .sd::after{content:"";position:absolute;box-sizing:border-box}
  .steps li[data-s="idle"] .sd::after{left:4px;top:4px;width:8px;height:8px;border:1px solid #cfcfcf}
  .steps li[data-s="done"] .sd::after{left:5px;top:2px;width:6px;height:10px;border:solid #9a9a9a;border-width:0 1.5px 1.5px 0;transform:rotate(40deg)}
  .steps li[data-s="on"] .sd i{opacity:.28;transform:scale(.72);animation:rc-wave 1.55s cubic-bezier(.42,0,.58,1) infinite;animation-delay:calc(var(--ph) + var(--w) * 140ms)}
  .sd i:nth-child(2){--w:1}.sd i:nth-child(3){--w:2}.sd i:nth-child(4){--w:3}.sd i:nth-child(5){--w:4}
  .sd i:nth-child(6){--w:5}.sd i:nth-child(7){--w:6}.sd i:nth-child(8){--w:7}.sd i:nth-child(9){--w:8}.sd i:nth-child(1){--w:0}
  @keyframes rc-wave{0%,100%{opacity:.28;transform:scale(.72);background:var(--ink)}45%{background:var(--blue)}50%{opacity:1;transform:scale(1);background:var(--blue)}65%{background:var(--ink)}}
  .sx{display:flex;flex-direction:column;min-width:0}
  .sk{font-size:17px;transition:color .3s ease}
  .st{font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sc{font-size:15px;font-variant-numeric:tabular-nums;display:inline-block;padding-top:2px;min-width:1ch;text-align:right}
  .steps li[data-s="idle"]{color:#b8b8b8}
  .steps li[data-s="idle"] .st{color:#c8c8c8}
  .steps li[data-s="on"]{color:var(--ink)}
  .steps li[data-s="on"] .sk{font-weight:500}
  /* the working line: a slow light sweeps across the words */
  .steps li[data-s="on"] .st{color:#6b6b6b;background:linear-gradient(110deg,#8a8a8a 30%,#111 50%,#8a8a8a 70%) 200% 0/200% 100%;
    -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;animation:rc-shim 1.8s linear infinite;animation-delay:var(--ph)}
  .steps li[data-s="on"] .st.chg{animation:rc-shim 1.8s linear infinite,rc-txt .2s ease-out}
  .st.chg{animation:rc-txt .2s ease-out}
  .sc.tick{animation:rc-tick .5s var(--ease)}
  @keyframes rc-shim{from{background-position:200% 0}to{background-position:-200% 0}}
  @keyframes rc-txt{from{opacity:0;transform:translateY(3px)}}
  @keyframes rc-tick{0%{transform:translateY(-7px);opacity:0;color:var(--blue)}60%{color:var(--blue)}}
  @media (prefers-reduced-motion: reduce){
    .steps li[data-s="on"] .sd i{opacity:.8;transform:none}
    .steps li[data-s="on"] .sd i:nth-child(5){background:var(--blue);opacity:1}
    .steps li[data-s="on"] .st{background:none;-webkit-text-fill-color:currentColor;color:#4a4a4a}
  }
  .chk{font-size:14px;line-height:1.4;color:var(--mute)}
  .run .chk{margin-top:24px}
  .res .chk{margin-top:8px}
  .chk:empty{display:none}
  .chkl{overflow-wrap:anywhere}
  .chkl.low{color:var(--ink)}
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
  .topl{margin-top:14px;font-size:16px;line-height:1.4;animation:rc-fade .5s ease .15s backwards}
  .res .field{margin-top:24px}
  /* sections: a hairline and a small label each */
  .sec{margin-top:22px;padding-top:12px;border-top:1px solid var(--line)}
  .sl{font-size:15px;line-height:1.3;font-weight:500;letter-spacing:.07em;text-transform:uppercase;color:var(--ink);margin-bottom:6px}
  /* every mismatch, one compact row each */
  .finds{list-style:none;margin:0 -12px}
  .finds.live{margin-top:24px;border-top:1px solid var(--line);padding-top:4px}
  .fr{padding-bottom:8px;border-top:1px solid #f0f0f0}
  .fr:first-child{border-top:0}
  .fr.in{animation:rc-rise .5s var(--ease) calc(.2s + min(var(--i), 8) * .08s) backwards}
  .find{display:flex;flex-direction:column;align-items:flex-start;gap:1px;width:100%;min-height:44px;padding:10px 12px 2px;transition:background-color .18s ease}
  .find:hover{background:#f4f4f4}
  .find.ping,.fr.ping{animation:rc-ping 1.2s ease-out}
  /* one blue, fainter as the run is less sure: high full, medium mid, low faint (tag, found value, left bar) */
  .fr{--acc:var(--blue);--acc-t:var(--blue)}
  .fr[data-shade="medium"]{--acc:#6585ea;--acc-t:#4a6fe6}
  .fr[data-shade="low"]{--acc:#b3c2f4;--acc-t:#8199ec}
  .find{box-shadow:inset 3px 0 0 var(--acc)}
  .fkr{display:flex;align-items:baseline;justify-content:space-between;gap:10px;width:100%}
  .fk{font-size:11px;line-height:1.3;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
  .ftag{flex:none;display:inline-flex;align-items:baseline;gap:5px;color:var(--acc-t);font-variant-numeric:tabular-nums;white-space:nowrap}
  .scn{font-size:30px;font-weight:300;line-height:1;letter-spacing:-.03em}
  .scw{font-size:22px;font-weight:300;line-height:1.1}
  .scu{font-size:12px;letter-spacing:.02em}
  .was{display:block;max-width:100%;font-size:17px;line-height:1.3;color:#858585;overflow-wrap:anywhere}
  .wt{text-decoration:none}
  s.wt{text-decoration-line:line-through;text-decoration-color:var(--acc,var(--blue));text-decoration-thickness:2px}
  .is{max-width:100%;font-size:26px;font-weight:300;line-height:1.1;letter-spacing:-.02em;color:var(--acc-t,var(--blue));overflow-wrap:anywhere}
  .is.m{font-size:22px}
  .is.s{font-size:19px;line-height:1.2}
  .fy{padding:3px 12px 0;font-size:14px;line-height:1.4;color:var(--ink)}
  .by{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:6px 12px 0;font-size:13px;color:var(--mute)}
  .by.bl{padding-top:2px}
  .citl{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
  .cite+.citl{margin-left:6px}
  .cite{display:inline-flex;align-items:center;gap:5px;min-height:28px;padding:2px 8px;border:1px solid var(--line);font-size:14px;line-height:1.2;color:var(--ink);text-decoration:none;background:#fff}
  .cite.dec{border-color:var(--ink)}
  .cite i{font-style:normal;font-size:12px;color:var(--blue)}
  a.cite:hover,a.cite:focus-visible,button.cite:hover{border-color:var(--blue);color:var(--blue)}
  .cw{display:inline-flex}
  button.cite{font:inherit;font-size:14px;cursor:pointer}
  .cite[aria-expanded=true]{border-color:var(--blue);color:var(--blue);box-shadow:inset 0 -2px 0 var(--blue)}
  /* source preview card (server/report_a.py's), anchored inside the drawer */
  .cc{position:absolute;z-index:5;left:0;top:0;width:320px;display:flex;flex-direction:column;gap:6px;padding:14px 16px 12px;background:#fff;color:var(--ink);
    border:1px solid var(--ink);border-top:3px solid var(--ink);box-shadow:0 18px 40px -18px rgba(0,0,0,.28);font-size:15px;line-height:1.35;
    opacity:0;transform:translateY(6px);clip-path:inset(0 0 100% 0);transition:opacity .14s ease-out,transform .14s ease-out,clip-path .14s ease-out}
  .cc[data-side=top]{transform:translateY(-6px);clip-path:inset(100% 0 0 0)}
  .cc.on{opacity:1;transform:none;clip-path:inset(0 0 0 0);transition:opacity .22s var(--ease),transform .22s var(--ease),clip-path .22s var(--ease)}
  .cc.r-decided{border-top-color:var(--blue)}
  .cc-h{display:flex;align-items:baseline;gap:10px;padding-right:26px}
  .cc-h b{font-weight:400;font-size:13px;letter-spacing:.08em;text-transform:uppercase}
  .cc-h em{font-style:normal;font-size:12px;letter-spacing:.06em;text-transform:uppercase;padding:1px 6px;border:1px solid var(--line);color:var(--mute);margin-left:auto;white-space:nowrap}
  .r-decided .cc-h em{background:var(--blue);border-color:var(--blue);color:#fff}
  .r-agrees .cc-h em{border-color:var(--ink);color:var(--ink)}
  .r-differs .cc-h em{border-color:var(--blue);color:var(--blue)}
  .cc-v{display:block;font-size:30px;font-weight:300;line-height:1.1;letter-spacing:-.02em;margin:4px 0 2px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
  .r-decided .cc-v{color:var(--blue)}
  .cc.on .cc-v,.cc.on .cc-read,.cc.on .cc-w{animation:rc-ccin .32s var(--ease) both}
  .cc.on .cc-read{animation-delay:.04s}.cc.on .cc-w{animation-delay:.08s}
  @keyframes rc-ccin{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
  .cc-read{display:block;overflow-wrap:anywhere}
  .cc-w{display:block;font-size:13px;color:var(--mute);overflow-wrap:anywhere}
  .cc-go{display:flex;align-items:center;justify-content:space-between;margin-top:6px;padding-top:9px;border-top:1px solid var(--line);color:var(--blue);text-decoration:none;font-size:15px;min-height:28px}
  .cc-go:hover{text-decoration:underline}.cc-go i{font-style:normal}
  .cc-go:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
  .cc-x{position:absolute;top:8px;right:8px;width:28px;height:28px;display:grid;place-items:center;font-size:20px;line-height:1;color:var(--mute);text-align:center}.cc-x:hover{color:var(--blue)}
  @media (max-width:520px){.cc-go{min-height:44px}.cc-x{width:44px;height:44px;top:4px;right:4px}}
  @media (prefers-reduced-motion: reduce){.cc,.cc[data-side=top],.cc.on{transform:none;clip-path:none}}
  .fln{flex:none;display:flex;gap:14px}
  .fln .lnk{min-height:28px;color:var(--ink);text-decoration:underline;text-underline-offset:3px;text-decoration-color:#bbb}
  /* which home was checked */
  .who{margin-top:16px;font-size:14px;line-height:1.4;color:var(--mute);overflow-wrap:anywhere;animation:rc-fade .5s ease backwards}
  .whw{color:var(--ink)}
  .whp{display:block}
  .res .who + .hl{margin-top:8px}
  /* how sure: a quiet chip under a finding, opens to the why and one line per source */
  .cf{padding:0 12px}
  .cfb{display:inline-flex;align-items:center;gap:7px;min-height:32px;font-size:13px;color:var(--mute);animation:rc-fade .5s ease .9s backwards}
  .cft{color:var(--ink)}
  .cfs{color:var(--ink);font-variant-numeric:tabular-nums}
  .cfdot{color:#9a9a9a}
  .bars{display:inline-flex;align-items:flex-end;gap:2px;height:11px}
  .bars i{display:block;width:3px;height:5px;background:#dcdcdc;transition:background-color .3s ease}
  .bars i:nth-child(2){height:8px}
  .bars i:nth-child(3){height:11px}
  .bars i.on{background:var(--acc,var(--ink))}
  .cf .go{display:grid;color:#9a9a9a;transition:transform .25s var(--ease)}
  .cf .go svg{width:14px;height:14px}
  button.cfb:hover .go{color:var(--ink)}
  .cfb[aria-expanded="true"] .go{transform:rotate(90deg)}
  .cfx{margin:2px 0 6px 5px;padding:2px 0 2px 12px;border-left:1px solid var(--line);animation:rc-rise .35s var(--ease)}
  .cfw{font-size:14px;line-height:1.4;color:var(--ink)}
  .cfd{margin-top:4px;font-size:13px;color:var(--mute)}
  .cfl{list-style:none;margin-top:6px;display:flex;flex-direction:column;gap:3px}
  .cfl li{display:flex;align-items:baseline;gap:8px;font-size:13px;line-height:1.35;min-width:0}
  .cn{flex:none;width:78px;color:var(--mute)}
  .cv{flex:0 1 auto;min-width:0;color:var(--ink);overflow-wrap:anywhere}
  .ct{flex:none;font-size:12px;color:var(--mute)}
  .ct.dec{color:var(--ink);border:1px solid var(--ink);padding:0 4px;line-height:1.35}
  .lnk:hover,.fln .lnk:hover{color:var(--blue)}
  .filine{width:100%;min-height:44px;display:flex;gap:12px;align-items:center;border-top:1px solid #f0f0f0;font-size:15px;color:var(--ink)}
  .ck .filine:first-child{border-top:0}
  .filine .fit{flex:1}
  .filine .go{display:grid;color:#9a9a9a;transition:transform .25s var(--ease)}
  .filine.open .go{transform:rotate(90deg)}
  .filine:hover .go{color:var(--ink)}
  .facts{list-style:none}
  .fact{padding:7px 0;border-top:1px solid #f0f0f0;animation:rc-rise .4s var(--ease) calc(var(--i) * 50ms) backwards}
  .fact:first-child{border-top:0}
  .fv{font-size:16px;line-height:1.35;color:var(--ink);overflow-wrap:anywhere}
  .fiall,.qall{min-height:36px;margin-top:2px;font-size:14px;text-decoration:underline;text-underline-offset:3px;text-decoration-color:#bbb;align-self:flex-start}
  /* questions to ask, each with Copy */
  .qs{list-style:none}
  .q{display:grid;grid-template-columns:18px minmax(0,1fr) auto;column-gap:8px;align-items:baseline;padding:8px 0;border-top:1px solid #f0f0f0;font-size:15px;line-height:1.4}
  .q:first-child{border-top:0}
  .qn{font-size:13px;color:var(--mute);font-variant-numeric:tabular-nums}
  .qt{overflow-wrap:anywhere}
  .qcopy{min-height:28px;font-size:13px;color:var(--mute);text-decoration:underline;text-underline-offset:3px;text-decoration-color:#ccc}
  /* what matched and what nobody could check: one plain line each */
  .cks{list-style:none;padding-bottom:6px}
  .ckr{display:flex;flex-wrap:wrap;column-gap:8px;align-items:baseline;padding:5px 0;font-size:15px;line-height:1.35}
  .ckl{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
  .ckt{min-width:0;overflow-wrap:anywhere}
  .ckw{flex-basis:100%;font-size:13px;color:var(--mute)}
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
