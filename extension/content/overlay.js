// Realty Check: on-page overlays (address highlights, claim marks, pill, tooltip).
// Drawn as absolutely positioned boxes inside our shadow root from Range.getClientRects(); page text is never touched.
(() => {
  const RC = (globalThis.RC = globalThis.RC || {});

  RC.OVERLAY_CSS = `
  .layer{position:absolute;left:0;top:0;width:0;height:0}
  .addr-mark{position:absolute;cursor:pointer;border-radius:2px;background:rgba(128,128,128,.12);box-shadow:inset 0 -1.5px 0 #111,0 1.5px 0 rgba(255,255,255,.75)}
  .addr-mark.in{animation:rcfade .3s ease-out}
  .addr-mark:hover,.addr-mark.sel{background:rgba(128,128,128,.24)}
  .claim-mark{position:absolute;cursor:pointer;border-radius:2px}
  .claim-mark::after{content:"";position:absolute;left:0;right:0;bottom:-2px;height:2px;border-radius:1px;background:var(--vc);box-shadow:0 0 0 1px rgba(255,255,255,.7)}
  .claim-mark.flash{animation:rcflash 1.7s ease-out}
  @keyframes rcfade{from{opacity:0}to{opacity:1}}
  @keyframes rcflash{0%,45%{background:color-mix(in srgb,var(--vc) 16%,transparent);box-shadow:0 0 0 4px color-mix(in srgb,var(--vc) 16%,transparent)}100%{background:transparent;box-shadow:0 0 0 10px transparent}}
  .pill{position:absolute;display:inline-flex;align-items:center;min-height:36px;padding:0 16px;border-radius:0;background:#111;color:#fff;
    font:400 15px/1 var(--serif);white-space:nowrap;border:0;cursor:pointer;animation:rcfade .2s ease-out;box-shadow:0 4px 14px rgba(0,0,0,.12)}
  .pill:hover{background:#333}
  .pill:focus-visible{outline:2px solid #1a4be0;outline-offset:3px}
  .tip{position:absolute;display:none;width:max-content;max-width:300px;padding:9px 12px;background:#fff;border:1px solid #e6e6e6;box-shadow:0 6px 20px rgba(0,0,0,.08);
    font:15px/1.4 var(--serif);color:#111;pointer-events:none}
  .tip.show{display:block}
  .tip .d{display:inline-block;width:8px;height:8px;margin-right:8px;vertical-align:1px}
  @media (prefers-reduced-motion: reduce){.addr-mark.in,.claim-mark.flash,.pill{animation:none}}
  `;

  RC.createOverlay = function createOverlay(root, host, cb) {
    const layer = document.createElement("div");
    layer.className = "layer";
    root.appendChild(layer);
    const tip = document.createElement("div");
    tip.className = "tip";
    root.appendChild(tip);
    let addresses = [], marks = [], selected = null, pill = null, enabled = true, flashing = new Set(), flashT = 0;
    const seen = new Set();

    // Is the text under this rect actually the top element there (not covered by a site modal)?
    const occluded = (range, rc) => {
      const cx = rc.left + Math.min(rc.width / 2, 12), cy = rc.top + rc.height / 2;
      if (cx < 0 || cy < 0 || cx >= innerWidth || cy >= innerHeight) return false;
      const hit = document.elementFromPoint(cx, cy);
      if (!hit || hit === host) return false;
      const c = range.commonAncestorContainer;
      const own = c.nodeType === 1 ? c : c.parentElement;
      return !(own && (hit === own || own.contains(hit) || hit.contains(own)));
    };

    // Stray marks: text that became hidden after the scan (closed menu, faded slide), or that sits outside a
    // clipping box (carousel slide parked off to the side, collapsed accordion with overflow:hidden).
    const ownEl = (range) => { const c = range.commonAncestorContainer; return c.nodeType === 1 ? c : c.parentElement; };
    const hidden = (range) => {
      const el = ownEl(range);
      if (!el || !el.isConnected) return true;
      return !!el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
    };
    const clipped = (range, rc) => {
      for (let el = ownEl(range); el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
        const cs = getComputedStyle(el);
        if (cs.position === "fixed") break;
        if (cs.overflowX === "visible" && cs.overflowY === "visible") continue;
        const b = el.getBoundingClientRect();
        if (rc.right <= b.left + 1 || rc.left >= b.right - 1 || rc.bottom <= b.top + 1 || rc.top >= b.bottom - 1) return true;
      }
      return false;
    };

    function layout() {
      if (!enabled) { layer.textContent = ""; return; }
      const sx = scrollX, sy = scrollY, boxes = [];
      const add = (range, cls, data, style) => {
        if (hidden(range)) return;
        for (const rc of range.getClientRects()) {
          if (rc.width < 1 || rc.height < 1 || clipped(range, rc) || occluded(range, rc)) continue;
          boxes.push([cls, data, `left:${rc.left + sx}px;top:${rc.top + sy}px;width:${rc.width}px;height:${rc.height}px;${style || ""}`]);
        }
      };
      addresses.forEach((a, i) => a.ranges.forEach((r) => add(r, "addr-mark" + (a.text === selected ? " sel" : "") + (seen.has(a.text) ? "" : " in"), { idx: i })));
      marks.forEach((m, i) => add(m.range, "claim-mark" + (flashing.has(i) ? " flash" : ""), { mi: i, verdict: m.verdict }, `--vc:${m.color}`));
      addresses.forEach((a) => seen.add(a.text));
      // Reuse the existing boxes: busy pages relayout often, and a box replaced between mousedown and mouseup
      // would swallow the user's click.
      boxes.forEach(([cls, data, css], i) => {
        let d = layer.children[i];
        if (!d) { d = document.createElement("div"); layer.appendChild(d); }
        if (d.className !== cls) d.className = cls;
        if (d.style.cssText !== css) d.style.cssText = css;
        for (const k of Object.keys(d.dataset)) if (!(k in data)) delete d.dataset[k];
        for (const k in data) if (d.dataset[k] !== String(data[k])) d.dataset[k] = data[k];
      });
      while (layer.children.length > boxes.length) layer.lastElementChild.remove();
      if (pill) placePill();
    }
    let raf = 0;
    const relayout = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; layout(); }); };
    addEventListener("scroll", relayout, { capture: true, passive: true });
    addEventListener("resize", relayout, { passive: true });
    const follow = (ms) => { const end = performance.now() + ms; const tick = () => { layout(); if (performance.now() < end) requestAnimationFrame(tick); }; requestAnimationFrame(tick); };

    function showPill(a) {
      hidePill();
      pill = document.createElement("button");
      pill.className = "pill";
      pill.textContent = "Inspect location";
      pill._addr = a;
      pill.addEventListener("click", (e) => { e.stopPropagation(); const addr = pill._addr; hidePill(); cb.onInspect(addr); });
      root.appendChild(pill);
      placePill();
    }
    function placePill() {
      const r = pill._addr.ranges[0] && pill._addr.ranges[0].getClientRects()[0];
      if (!r) return hidePill();
      const vw = document.documentElement.clientWidth;
      let left = r.right + 8, top = r.top + r.height / 2 - 14;
      if (left + 140 > vw) { left = r.left; top = r.bottom + 6; }
      pill.style.left = left + scrollX + "px";
      pill.style.top = top + scrollY + "px";
    }
    function hidePill() { if (pill) { pill.remove(); pill = null; } }
    document.addEventListener("click", (e) => { if (pill && !e.composedPath().includes(pill) && !e.composedPath().includes(layer)) hidePill(); }, true);

    layer.addEventListener("click", (e) => {
      const a = e.target.closest(".addr-mark");
      if (a) { e.stopPropagation(); e.preventDefault(); showPill(addresses[+a.dataset.idx]); return; }
      const m = e.target.closest(".claim-mark");
      if (m) { e.stopPropagation(); e.preventDefault(); cb.onMark(marks[+m.dataset.mi].claimId); }
    });
    layer.addEventListener("mouseover", (e) => {
      const el = e.target.closest(".claim-mark");
      if (!el) return;
      const m = marks[+el.dataset.mi];
      tip.innerHTML = "";
      const dot = document.createElement("span");
      dot.className = "d";
      dot.style.background = m.color;
      tip.append(dot, document.createTextNode(m.tip));
      tip.className = "tip show";
      const L = parseFloat(el.style.left), T = parseFloat(el.style.top);
      const maxL = scrollX + document.documentElement.clientWidth - 310;
      tip.style.left = Math.max(scrollX + 8, Math.min(L, maxL)) + "px";
      const h = tip.offsetHeight;
      tip.style.top = (T - scrollY - h - 8 > 0 ? T - h - 8 : T + parseFloat(el.style.height) + 8) + "px";
    });
    layer.addEventListener("mouseout", (e) => { if (e.target.closest(".claim-mark")) tip.className = "tip"; });

    return {
      setAddresses(list) { addresses = list; relayout(); },
      setSelected(t) { selected = t; relayout(); },
      setMarks(list) { marks = list; flashing = new Set(); relayout(); },
      // Scroll the page to the words of one claim and flash their mark. False when no visible mark exists.
      flash(claimId) {
        const idx = [];
        marks.forEach((m, i) => { if ((m.claimIds || [m.claimId]).includes(claimId) && !hidden(m.range)) idx.push(i); });
        if (!idx.length) return false;
        const el = ownEl(marks[idx[0]].range);
        const smooth = !matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (el) el.scrollIntoView({ block: "center", inline: "nearest", behavior: smooth ? "smooth" : "auto" });
        flashing = new Set();
        layout(); // drop the old class so the animation restarts
        flashing = new Set(idx);
        follow(900);
        clearTimeout(flashT);
        flashT = setTimeout(() => { flashing = new Set(); relayout(); }, 1800);
        return true;
      },
      setEnabled(v) { enabled = v; relayout(); },
      layout, relayout, follow, hidePill,
      count: () => addresses.length,
    };
  };
})();
