// Reality Check: edge tab, drawer and run flow. Everything lives in ONE shadow root on <div id="rc-host">.
(() => {
  if (window.top !== window || document.getElementById("rc-host")) return;
  const RC = globalThis.RC;
  const W = 380;
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const VC = { contradicted: "#dc2626", close: "#d97706", confirmed: "#16a34a", cant_check: "#a3a3a3" };
  const VL = { contradicted: "Mismatch", close: "Mismatch", confirmed: "Match", cant_check: "Can't verify" };
  const SRC = { scan: "3D scan", aerial: "Aerial measurement", county: "County record", record: "Building record" };
  const AG = { orchestrator: "Lead", claims: "Claims", matcher: "Match", inside: "Inside", outside: "Outside", paper: "Records", judge: "Judge" };
  const LOGO = `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><circle cx="12" cy="12" r="9.5" fill="none" stroke="#2563eb" stroke-width="2"/><path d="M7.8 12.4l2.9 2.9 5.6-5.9" fill="none" stroke="#2563eb" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const GEAR = `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>`;
  const CHEV = `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>`;
  const X = `<svg viewBox="0 0 12 12" width="8" height="8"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const send = (msg) => { try { chrome.runtime.sendMessage(msg); } catch (e) { /* extension reloaded */ } };

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
    <button class="tab-face" id="tabFace" aria-label="Open Reality Check" title="Reality Check">${LOGO}</button>
  </div>
  <aside class="drawer" id="drawer" aria-label="Reality Check">
    <header class="hd">
      <div class="brand">${LOGO}<span>Reality Check</span></div>
      <div class="act">
        <button class="hlink" id="btnReport" disabled>Report</button>
        <button class="icon" id="btnGear" aria-label="Settings" title="Settings">${GEAR}</button>
        <button class="icon" id="btnCollapse" aria-label="Close" title="Close">${CHEV}</button>
      </div>
    </header>
    <div class="menu" id="menu" hidden>
      <label for="srv">Server address</label>
      <div class="menu-row"><input id="srv" spellcheck="false" autocomplete="off"><button class="btn secondary" id="srvSave">Save</button></div>
      <p class="hint" id="srvHint">Where the local checker runs.</p>
    </div>
    <div class="body" id="body"></div>
    <footer class="ft" id="ft" hidden>
      <button class="btn primary" id="btnFull">Open full report</button>
      <button class="textbtn" id="btnAnother">Check another address</button>
    </footer>
  </aside>`;
  const $ = (id) => root.getElementById(id);
  const tab = $("tab"), drawer = $("drawer"), body = $("body");

  // ---------- state ----------
  let addresses = [], flat = null;
  let run = null; // {address, mode, claims:[], verdicts:{}, step, done, unreachable, error, token}
  let open = false;
  const expanded = new Set();
  const ORDER = { contradicted: 0, close: 1, confirmed: 2, cant_check: 9 };
  let ccOpen = false;
  const imgCache = new Map();

  const overlay = RC.createOverlay(root, host, {
    onInspect: (a) => { openDrawer(); startRun(a); },
    onMark: (cid) => { openDrawer(); expanded.add(cid); if (run && run.verdicts[cid] && run.verdicts[cid].verdict === "cant_check") ccOpen = true; render(); const el = body.querySelector(`[data-id="${cid}"]`); if (el) el.scrollIntoView({ block: "nearest", behavior: REDUCED ? "auto" : "smooth" }); },
  });

  // ---------- detection ----------
  function scan() {
    const nodes = RC.textNodes();
    flat = RC.flatten(nodes);
    addresses = RC.scanAddresses(nodes, flat);
    host.dataset.count = String(addresses.length);
    overlay.setAddresses(addresses);
    rebuildMarks();
    if (open && !run) render();
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
        ? `Listing says ${v.claimed}. We can't verify this.`
        : `Listing says ${v.claimed}. ${SRC[v.source] || "We"} ${SRC[v.source] ? "shows" : "found"} ${v.found}.`;
      const g = groups.get(c.quote);
      if (!g) groups.set(c.quote, { claimId: c.id, verdict: v.verdict, tips: [tip] });
      else {
        g.tips.push(tip);
        if ((ORDER[v.verdict] ?? 9) < (ORDER[g.verdict] ?? 9)) { g.verdict = v.verdict; g.claimId = c.id; }
      }
    }
    const marks = [];
    for (const [quote, g] of groups) {
      const tip = g.tips.join(" ");
      for (const range of RC.findText(flat, quote, 3)) marks.push({ range, claimId: g.claimId, verdict: g.verdict, color: VC[g.verdict] || VC.cant_check, tip });
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
    if (!REDUCED) de.style.setProperty("transition", "margin-right .2s ease", "important");
    de.style.setProperty("margin-right", W + "px", "important");
    pushFixed();
    drawer.classList.add("open");
    tab.classList.add("away");
    render();
    overlay.follow(320);
  }
  function closeDrawer() {
    if (!open) return;
    open = false;
    de.style.setProperty("margin-right", "0px", "important");
    unpushFixed();
    drawer.classList.remove("open");
    tab.classList.remove("away");
    $("menu").hidden = true;
    setTimeout(() => {
      if (open) return;
      setProp(de, "margin-right", saved.mr); setProp(de, "transition", saved.tr);
      overlay.layout();
    }, REDUCED ? 0 : 220);
    overlay.follow(320);
  }
  $("btnCollapse").addEventListener("click", closeDrawer);
  $("btnGear").addEventListener("click", () => {
    const m = $("menu");
    m.hidden = !m.hidden;
    if (!m.hidden) try { chrome.storage.local.get("rcServer", (r) => { $("srv").value = (r && r.rcServer) || "http://127.0.0.1:8787"; }); } catch (e) { /* no storage */ }
  });
  $("srvSave").addEventListener("click", () => {
    const v = $("srv").value.trim().replace(/\/+$/, "") || "http://127.0.0.1:8787";
    try { chrome.storage.local.set({ rcServer: v }, () => { $("srvHint").textContent = "Saved."; setTimeout(() => { $("srvHint").textContent = "Where the local checker runs."; }, 1500); }); } catch (e) { /* no storage */ }
  });
  const openReport = () => {
    if (!run || !run.done) return;
    if (run.mode === "sample") send({ type: "openSampleReport", address: run.address.full });
    else send({ type: "openReport", report_url: run.done.report_url });
  };
  $("btnReport").addEventListener("click", openReport);
  $("btnFull").addEventListener("click", openReport);
  $("btnAnother").addEventListener("click", () => { stopRun(); run = null; expanded.clear(); rebuildMarks(); overlay.setSelected(null); render(); });

  // ---------- run ----------
  let port = null;
  function stopRun() {
    if (port) { try { port.disconnect(); } catch (e) { /* closed */ } port = null; }
    if (run) run.token = null;
  }
  function startRun(a) {
    stopRun();
    expanded.clear();
    ccOpen = false;
    run = { address: a, mode: "server", claims: [], verdicts: {}, step: "Connecting to the local checker…", steps: [], t0: Date.now(), done: null, unreachable: false, error: null, token: {} };
    overlay.setSelected(a.text);
    rebuildMarks();
    render();
    const snapshot = {
      url: location.href, title: document.title, address_text: a.full,
      text: (document.body ? document.body.innerText : "").slice(0, 60000), jsonld: RC.jsonld(),
    };
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
    run = { address: a, mode: "sample", claims: [], verdicts: {}, step: "Starting…", steps: [], t0: Date.now(), done: null, unreachable: false, error: null, token: {} };
    const me = run;
    render();
    RC_SAMPLE.events(a.full).forEach((ev, i) => setTimeout(() => { if (run === me && me.token) handle(ev); }, (i + 1) * RC_SAMPLE.delayMs));
  }
  function handle(ev) {
    if (!run) return;
    switch (ev.type) {
      case "step": {
        // One line per agent, newest text wins; the line keeps its first position so the list never jumps.
        run.step = ev.text;
        const k = ev.agent || "orchestrator", s = run.steps.find((x) => x.agent === k);
        if (s) { s.text = ev.text; s.t = Date.now(); } else run.steps.push({ agent: k, text: ev.text, t: Date.now() });
        run.last = k;
        break;
      }
      case "claims": run.claims = ev.claims || []; break;
      case "verdict": run.verdicts[ev.verdict.claim_id] = ev.verdict; rebuildMarks(); if (ev.verdict.image && run.mode === "server") loadImg(ev.verdict.image); break;
      case "done": run.done = ev; run.step = ""; break;
      case "error": run.error = ev.message || "Something went wrong."; break;
      default: return; // run_started, match, evidence: nothing to draw
    }
    render();
  }
  function loadImg(path) {
    if (imgCache.has(path)) return;
    imgCache.set(path, null);
    try {
      chrome.runtime.sendMessage({ type: "img", path }, (r) => { void chrome.runtime.lastError; if (r && r.dataUrl) { imgCache.set(path, r.dataUrl); render(); } });
    } catch (e) { /* extension reloaded */ }
  }

  // ---------- progress (a long run must never look frozen) ----------
  const elapsed = (r) => Math.floor((Date.now() - r.t0) / 1000) + " s";
  function pct(r) {
    // Before claims arrive: creep slowly toward 14% with time. After: 15% + share of verdicts in.
    const n = r.claims.length, nv = Object.keys(r.verdicts).length, t = (Date.now() - r.t0) / 1000;
    const v = n ? 15 + (85 * nv) / n : 3 + 11 * (1 - Math.exp(-t / 25));
    r.pmax = Math.max(r.pmax || 0, v);
    return r.pmax.toFixed(1);
  }
  setInterval(() => {
    if (!open || !run || run.done || run.error || run.unreachable) return;
    const el = $("elapsed"), bf = $("barFill");
    if (el) el.textContent = elapsed(run);
    if (bf) bf.style.width = pct(run) + "%";
  }, 1000);

  // ---------- render ----------
  function render() {
    $("btnReport").disabled = !(run && run.done);
    $("ft").hidden = !(run && run.done);
    if (!run) return renderIdle();
    const r = run, n = r.claims.length, nv = Object.keys(r.verdicts).length;
    let h = `<div class="eyebrow">${r.done ? "Checked" : "Checking"}${r.mode === "sample" ? " · sample result" : ""}</div><div class="addr">${esc(r.address.full)}</div>`;
    if (r.unreachable) {
      h += `<div class="notice"><span class="d"></span>Local checker is not running.</div>
        <div class="btnrow"><button class="btn secondary" id="useSample">Use sample result</button><button class="textbtn" id="retry">Try again</button></div>`;
    } else if (r.error) {
      h += `<div class="notice"><span class="d" style="background:${VC.contradicted}"></span>${esc(r.error)}</div>`;
    } else if (r.done) {
      const c = r.done.counts || {};
      h += `<p class="headline" id="headline">${esc(r.done.headline)}</p><div class="counts">` +
        [["contradicted", (c.contradicted || 0) + (c.close || 0)], ["confirmed", c.confirmed || 0], ["cant_check", c.cant_check || 0]]
          .map(([k, n]) => `<div class="cnt"><b>${n}</b><span><i class="d" style="background:${VC[k]}"></i>${VL[k]}</span></div>`).join("") + `</div>`;
    } else {
      h += `<div class="prog"><div class="bar"><i id="barFill" style="width:${pct(r)}%"></i></div>` +
        `<div class="pmeta"><span>${r.steps.length ? "Working" : esc(r.step)}</span><span class="el" id="elapsed">${elapsed(r)}</span></div>` +
        (r.steps.length ? `<ul class="steps" id="step">` + r.steps.map((s) =>
          `<li class="${s.agent === r.last ? "now" : ""}"><span class="d"></span><span class="ag">${esc(AG[s.agent] || s.agent)}</span><span class="tx">${esc(s.text)}</span></li>`).join("") + `</ul>` : "") +
        `</div>`;
    }
    if (n) {
      // Mismatch first (contradicted, then close), match, still waiting; "can't verify" rows fold under one line.
      const rank = (c) => { const v = r.verdicts[c.id]; return v ? ORDER[v.verdict] ?? 2 : 3; };
      const shown = r.claims.filter((c) => rank(c) < 9).map((c, i) => [rank(c), i, c]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((x) => x[2]);
      const cc = r.claims.filter((c) => rank(c) === 9);
      h += `<div class="sec">What the listing says</div><ul class="claims">` + shown.map((c) => claimHTML(c, r.verdicts[c.id])).join("") + `</ul>`;
      if (cc.length) {
        h += `<button class="ccline${ccOpen ? " open" : ""}" id="ccToggle" aria-expanded="${ccOpen}"><span class="d"></span><span class="cct">Can't verify (${cc.length})</span><span class="go">${CHEV}</span></button>`;
        if (ccOpen) h += `<ul class="claims cc">` + cc.map((c) => claimHTML(c, r.verdicts[c.id])).join("") + `</ul>`;
      }
    }
    body.innerHTML = h;
    const us = $("useSample"); if (us) us.addEventListener("click", startSample);
    const rt = $("retry"); if (rt) rt.addEventListener("click", () => startRun(r.address));
  }
  function said(c) {
    const n = Number(String(c.value).replace(/,/g, "")), s = (w) => (n === 1 ? w : w + "s");
    if (!Number.isFinite(n)) return String(c.value || c.quote);
    if (c.kind === "bedrooms") return n === 0 ? "Studio" : `${n} ${s("bedroom")}`;
    if (c.kind === "bathrooms") return `${n} ${s("bathroom")}`;
    if (c.kind === "area_sqft") return `${n.toLocaleString("en-US")} sq ft`;
    return c.quote;
  }
  function claimHTML(c, v) {
    // A quote shared by several claims would repeat on every row: title those rows by what is claimed.
    const shared = run && run.claims.filter((x) => x.quote === c.quote).length > 1;
    const title = shared ? said(c) : c.quote;
    const color = v ? VC[v.verdict] || VC.cant_check : "#d4d4d4";
    const sub = !v ? `<div class="sub wait">Waiting…</div>`
      : v.verdict === "cant_check" ? `<div class="sub">We can't verify this</div>` : `<div class="sub">We found ${esc(v.found)}</div>`;
    const more = v ? `<div class="more"><dl class="kv"><dt>Listing says</dt><dd>${esc(v.claimed)}</dd><dt>We found</dt><dd>${esc(v.found)}</dd><dt>Source</dt><dd>${esc(SRC[v.source] || v.source)}</dd></dl>
      ${v.reason ? `<p class="reason">${esc(v.reason)}</p>` : ""}${v.image && imgCache.get(v.image) ? `<img class="proof" alt="Proof image" src="${imgCache.get(v.image)}">` : ""}</div>` : "";
    return `<li class="claim${expanded.has(c.id) && v ? " open" : ""}" data-id="${esc(c.id)}" data-verdict="${v ? v.verdict : "waiting"}">
      <div class="row1"><span class="d" style="background:${color}"></span><div class="main"><div class="q">${esc(title)}${c.label ? `<span class="lbl">${esc(c.label)}</span>` : ""}</div>${sub}</div>
      ${v ? `<span class="vl" style="color:${color}">${VL[v.verdict] || ""}</span>` : ""}</div>${more}</li>`;
  }
  body.addEventListener("click", (e) => {
    const row = e.target.closest(".row1");
    if (row) {
      const li = row.parentElement, id = li.dataset.id;
      if (li.dataset.verdict === "waiting") return;
      if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
      li.classList.toggle("open");
      return;
    }
    if (e.target.closest("#ccToggle")) { ccOpen = !ccOpen; render(); return; }
    const pick = e.target.closest(".pick");
    if (pick) startRun(addresses[+pick.dataset.i]);
  });
  function renderIdle() {
    if (!addresses.length) {
      body.innerHTML = `<div class="sec first">Addresses on this page</div><p class="empty">No street address found on this page.</p>`;
      return;
    }
    body.innerHTML = `<div class="sec first">Addresses on this page</div><p class="hint top">Click a highlighted address on the page, or pick one here.</p><ul class="picks">` +
      addresses.map((a, i) => `<li><button class="pick" data-i="${i}"><span class="pa">${esc(a.full)}</span><span class="go">${CHEV}</span></button></li>`).join("") + `</ul>`;
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
  .layer,.drawer,.tab,.pill,.tip{--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-family:var(--sans);color:#111;-webkit-font-smoothing:antialiased;letter-spacing:normal;text-transform:none;line-height:1.5;text-align:left}
  button{font:inherit;color:inherit;cursor:pointer;background:none;border:0;padding:0;margin:0}
  button:focus-visible,input:focus-visible{outline:2px solid rgba(37,99,235,.45);outline-offset:1px}
  [hidden]{display:none!important}

  .tab{position:fixed;right:0;height:44px;display:flex;align-items:stretch;background:#fff;border:1px solid #e6e6e6;border-right:0;border-radius:10px 0 0 10px;
    transition:transform .18s ease,opacity .18s ease;user-select:none}
  .tab.away{transform:translateX(110%);opacity:0;pointer-events:none}
  .tab.gone{display:none}
  .tab-face{width:42px;display:grid;place-items:center}
  .tab-handle{width:0;overflow:hidden;opacity:0;display:grid;grid-template-columns:repeat(2,3px);grid-auto-rows:3px;gap:3px;align-content:center;justify-content:center;
    cursor:grab;transition:width .18s ease,opacity .18s ease}
  .tab:hover .tab-handle,.tab.dragging .tab-handle{width:16px;opacity:1}
  .tab.dragging .tab-handle{cursor:grabbing}
  .tab-handle i{width:3px;height:3px;border-radius:50%;background:#b5b5b5}
  .tab-close{position:absolute;left:-8px;top:-8px;width:18px;height:18px;border-radius:50%;background:#fff;border:1px solid #e6e6e6;color:#6b6b6b;
    display:grid;place-items:center;opacity:0;transition:opacity .15s ease}
  .tab:hover .tab-close{opacity:1}
  .tab-close:hover{color:#111}

  .drawer{position:fixed;top:0;right:0;bottom:0;width:${W}px;background:#fff;border-left:1px solid #e6e6e6;box-shadow:-6px 0 24px rgba(0,0,0,.05);
    display:flex;flex-direction:column;font-size:14px;transform:translateX(100%);visibility:hidden;transition:transform .2s ease,visibility 0s linear .2s}
  .drawer.open{transform:none;visibility:visible;transition:transform .2s ease}
  .hd{flex:none;height:52px;display:flex;align-items:center;justify-content:space-between;padding:0 10px 0 18px;border-bottom:1px solid #e6e6e6}
  .brand{display:flex;align-items:center;gap:9px;font-weight:600;font-size:14px}
  .brand svg{display:block}
  .act{display:flex;align-items:center;gap:2px}
  .hlink{font-size:13px;color:#2563eb;padding:6px 8px;border-radius:6px;margin-right:2px}
  .hlink:hover:not(:disabled){background:#f4f6fb}
  .hlink:disabled{color:#b5b5b5;cursor:default}
  .icon{width:30px;height:30px;border-radius:8px;color:#6b6b6b;display:grid;place-items:center}
  .icon:hover{background:#f5f5f5;color:#111}
  .menu{flex:none;padding:14px 18px 12px;border-bottom:1px solid #e6e6e6;background:#fff}
  .menu label{display:block;font-size:12px;color:#444;margin-bottom:6px}
  .menu-row{display:flex;gap:8px}
  .menu input{flex:1;min-width:0;height:34px;border:1px solid #e6e6e6;border-radius:8px;padding:0 10px;font:13px var(--sans);color:#111;background:#fff}
  .menu input:focus{border-color:#2563eb;outline:none}
  .hint{font-size:12px;color:#6b6b6b;margin:6px 0 0}
  .hint.top{margin:0 0 10px}

  .body{flex:1;overflow-y:auto;padding:20px 18px 24px;scrollbar-width:thin;scrollbar-color:#e0e0e0 transparent}
  .eyebrow{font-size:12px;color:#6b6b6b;margin-bottom:2px}
  .addr{font-size:15px;font-weight:600;line-height:1.4}
  .prog{margin-top:18px}
  .bar{height:2px;background:#eef0f3;border-radius:2px;overflow:hidden}
  .bar i{display:block;height:100%;background:#2563eb;transition:width .2s ease}
  .pmeta{display:flex;justify-content:space-between;margin-top:8px;font-size:13px;color:#444}
  .pmeta .el{color:#a3a3a3;font-variant-numeric:tabular-nums}
  .steps{list-style:none;margin:8px 0 0;padding:0}
  .steps li{display:flex;gap:10px;align-items:baseline;padding:3px 0;font-size:13px;color:#6b6b6b}
  .steps .d{width:6px;height:6px;margin:7px 0 0;align-self:flex-start;background:#d4d4d4}
  .steps .ag{flex:none;width:56px;color:#a3a3a3;font-size:12px}
  .steps .tx{flex:1;min-width:0}
  .steps li.now{color:#111}
  .steps li.now .d{background:#2563eb;animation:rcpulse 1.2s ease-in-out infinite}
  @keyframes rcpulse{50%{opacity:.25}}
  .headline{margin:16px 0 0;font-size:15px;line-height:1.45;font-weight:500;color:#111}
  .counts{display:grid;grid-template-columns:repeat(3,1fr);margin-top:16px;border:1px solid #e6e6e6;border-radius:10px}
  .cnt{padding:10px 10px 9px;border-left:1px solid #e6e6e6}
  .cnt:first-child{border-left:0}
  .cnt b{display:block;font-size:16px;font-weight:600;line-height:1.2}
  .cnt span{display:block;font-size:11.5px;color:#6b6b6b;white-space:nowrap;margin-top:2px}
  .cnt .d{display:inline-block;width:6px;height:6px;margin:0 5px 0 0;vertical-align:1px}
  .notice{display:flex;gap:10px;align-items:center;margin-top:18px;font-size:13.5px;color:#444}
  .notice .d{margin:0;background:#a3a3a3}
  .btnrow{display:flex;gap:12px;align-items:center;margin-top:12px}

  .sec{font-size:12px;color:#6b6b6b;margin:26px 0 6px}
  .sec.first{margin-top:0;margin-bottom:4px}
  .claims,.picks{list-style:none;margin:0;padding:0}
  .claim{border-bottom:1px solid #e6e6e6}
  .claim:last-child{border-bottom:0}
  .row1{display:flex;gap:12px;align-items:flex-start;padding:12px 0;cursor:pointer}
  .claim[data-verdict="waiting"] .row1{cursor:default}
  .d{width:8px;height:8px;border-radius:50%;flex:none;margin-top:7px;background:#d4d4d4;transition:background .2s ease}
  .main{flex:1;min-width:0}
  .q{font-size:14px;color:#111}
  .lbl{font-size:12px;color:#6b6b6b;margin-left:8px}
  .sub{font-size:13px;color:#444;margin-top:1px}
  .sub.wait{color:#a3a3a3}
  .vl{flex:none;font-size:12px;margin-top:2px}
  .ccline{width:100%;display:flex;gap:12px;align-items:center;padding:12px 0;border-top:1px solid #e6e6e6;font-size:13px;color:#6b6b6b;text-align:left}
  .ccline .d{margin:0;background:#a3a3a3}
  .ccline .cct{flex:1}
  .ccline .go{display:grid;color:#b5b5b5;transition:transform .15s ease}
  .ccline.open .go{transform:rotate(90deg)}
  .ccline:hover{color:#111}
  .claims.cc{border-top:1px solid #e6e6e6}
  .more{display:none;padding:0 0 14px 20px}
  .claim.open .more{display:block}
  .kv{display:grid;grid-template-columns:92px 1fr;gap:4px 12px;margin:0;font-size:13px}
  .kv dt{color:#6b6b6b}
  .kv dd{margin:0;color:#111}
  .reason{font-size:13px;color:#444;margin:10px 0 0}
  .proof{display:block;width:100%;max-height:300px;object-fit:contain;background:#fff;margin-top:10px;border-radius:8px;border:1px solid #e6e6e6}

  .picks li{border-bottom:1px solid #e6e6e6}
  .picks li:last-child{border-bottom:0}
  .pick{width:100%;display:flex;align-items:center;gap:10px;padding:12px 0;text-align:left;font-size:14px;color:#111}
  .pick .pa{flex:1}
  .pick .go{color:#b5b5b5;display:grid}
  .pick:hover .go{color:#2563eb}
  .empty{font-size:13.5px;color:#6b6b6b;margin:0}

  .ft{flex:none;display:flex;align-items:center;gap:14px;padding:14px 18px;border-top:1px solid #e6e6e6;background:#fff}
  .btn{height:36px;padding:0 14px;border-radius:8px;font-size:13.5px;font-weight:500;white-space:nowrap;transition:background .15s ease}
  .primary{background:#2563eb;color:#fff}
  .primary:hover{background:#1d4ed8}
  .secondary{background:#fff;border:1px solid #e6e6e6;color:#111}
  .secondary:hover{background:#f7f7f7}
  .textbtn{font-size:13.5px;color:#444;padding:8px 2px}
  .textbtn:hover{color:#111}
  @media (prefers-reduced-motion: reduce){*{transition:none!important;animation:none!important}}
  `; }
})();
